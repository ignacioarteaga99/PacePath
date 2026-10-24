"""Zonas de entrenamiento y revisión de 3 meses (pestaña «Entreno»)."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from pacepath import entreno as e
from pacepath.api import app

HOY = date(2026, 10, 24)                      # sábado


def test_vdot_de_daniels():
    assert e.vdot(10_000, 40 * 60) == pytest.approx(51.9, abs=0.1)          # tabla de Daniels
    v = e.vdot(10_000, 40 * 60)
    assert e.tiempo_equivalente(v, 21_097.5) / 60 == pytest.approx(88.4, abs=0.6)
    assert 240 <= e.ritmo_umbral_de_vdot(v) <= 255                          # ~4:08/km


def test_zonas_por_fuente():
    z = e.zonas({"fc_max": 190})
    assert [r["hasta"] for r in z["fc"][:4]] == [137, 156, 165, 175] and z["fuente_fc"] == "fc_max"
    assert e.zonas({"fc_max": 190, "fc_reposo": 50})["fuente_fc"] == "reserva"
    u = e.zonas({"fc_max": 190, "fc_umbral": 170})                           # la FC umbral manda
    assert u["fuente_fc"] == "umbral" and u["fc_umbral"] == 170
    assert e.zonas({}, fc_max_detectada=185)["fuente_fc"] == "fc_max_detectada"
    r = e.zonas({"ritmo_umbral_s": 240, "t10k_s": 2400})
    assert r["fuente_ritmo"] == "umbral" and r["ritmo_umbral_s"] == 240 and r["vdot"] == 51.9
    assert [x["hasta"] for x in r["ritmo"][:4]] == [300, 267, 240, 229]       # cada vez más rápido
    assert e.zonas({"t21k_s": 90 * 60})["fuente_ritmo"] == "vdot"
    assert e.zonas({})["fc"] is None and e.zonas({})["ritmo"] is None


def _carrera(dia: date, km: float, min_: float, hist_fc=None, **kw):
    c = {"id": hash((dia, km)), "fecha_local": f"{dia.isoformat()}T07:00:00Z", "distancia_m": km * 1000,
         "tiempo_s": min_ * 60, "nombre": kw.pop("nombre", "Rodaje")}
    if hist_fc:
        c["hist"] = {"fc": hist_fc, "fc_max": max(b for b, _ in hist_fc)}
    return c | kw


def _semana_noruega(lunes: date) -> list[dict]:
    """5 rodajes suaves (I1–I2), 2 de umbral (25' en I3–I4) y un fondo."""
    suave = [[130, 40 * 60], [150, 5 * 60]]
    umbral = [[135, 30 * 60], [160, 15 * 60], [168, 10 * 60]]
    out = [_carrera(lunes + timedelta(days=d), 9, 45, suave) for d in (0, 2, 4)]
    out += [_carrera(lunes + timedelta(days=d), 12, 55, umbral, nombre="5x6' umbral", tipo_entreno=3) for d in (1, 3)]
    out.append(_carrera(lunes + timedelta(days=6), 20, 110, [[135, 100 * 60], [150, 10 * 60]], tipo_entreno=2))
    return out


def test_revision_de_un_entreno_noruego():
    lunes = HOY - timedelta(days=HOY.weekday())
    carreras = [c for i in range(12) for c in _semana_noruega(lunes - timedelta(weeks=i))
                if c["fecha_local"][:10] <= HOY.isoformat()]
    r = e.revision(carreras, {"fc_max": 190, "fase": "basica"}, hoy=HOY)["revision"]
    assert r["fuente"] == "fc" and r["detalle_pct"] == 100 and len(r["semanas"]) == 12
    assert r["forma"] == "piramidal" and 75 <= sum(r["distribucion"][:2]) <= 90
    estados = {c["clave"]: c["estado"] for c in r["chequeos"]}
    assert estados["umbral"] == "ok" and estados["i5"] == "ok" and estados["zona_gris"] == "ok"
    assert {s["tipo"] for s in r["sesiones"]} >= {"suave", "umbral", "fondo"}


def test_zona_gris_y_sin_detalle():
    lunes = HOY - timedelta(days=HOY.weekday())
    # Todo rodaje a ritmo «medio» (I3) sin estructura: la zona gris.
    gris = [_carrera(lunes - timedelta(weeks=w, days=-d), 10, 50, [[150, 25 * 60], [160, 25 * 60]])
            for w in range(4) for d in (0, 2, 4)]
    r = e.revision(gris, {"fc_max": 190}, hoy=HOY)["revision"]
    estados = {c["clave"]: c["estado"] for c in r["chequeos"]}
    assert estados["suave"] == "mal"
    assert r["sesiones"][0]["tipo"] == "umbral"          # 25' en I3: cuenta como umbral, pero ya no es «suave»
    # Sin detalle de Strava: la carrera entera en la zona de su FC media.
    solo = [_carrera(HOY - timedelta(days=1), 10, 50, fc_media=140)]
    r2 = e.revision(solo, {"fc_max": 190}, hoy=HOY)["revision"]
    assert r2["fuente"] == "fc_media" and r2["detalle_pct"] == 0 and r2["distribucion"][1] == 100


def test_endpoint_revision():
    cli = TestClient(app)
    j = cli.post("/api/entreno/revision", json={"ajustes": {"t10k_s": 2400, "fase": "especifica"},
                                                "carreras": [], "hoy": HOY.isoformat()}).json()
    assert j["zonas"]["vdot"] == 51.9 and j["zonas"]["ritmo"] and j["revision"]["vacia"]
    assert cli.post("/api/entreno/revision", json={"ajustes": {}, "carreras": []}).json()["revision"] is None


def test_histograma_de_strava(monkeypatch):
    """Segundos por pulsación y por ritmo, sólo en movimiento, sin pausas ni picos del sensor."""
    from pacepath import strava
    n = 600
    datos = {"time": {"data": list(range(n)) + [n + 300]},                 # una pausa de 5 min al final
             "heartrate": {"data": [140] * 300 + [165] * 299 + [230, 150]},  # 230: pico del sensor (1 s)
             "velocity_smooth": {"data": [3.0] * 300 + [4.0] * 300 + [4.0]},
             "moving": {"data": [True] * (n + 1)}}
    monkeypatch.setattr(strava, "_http", lambda *a, **k: datos)
    h = strava.histograma(strava.Sesion("t", "r", 9e9, 1), 123)
    fc = dict(h["fc"])
    assert fc[140] == 299 and fc[165] == 299 and h["fc_max"] == 165 and h["s"] == 599
    assert dict(h["ritmo"]) == {330: 299, 250: 300}                       # 5:33/km → 5:30 · 4:10


def test_carreras_en_cinta_van_aparte(monkeypatch):
    from pacepath import strava
    acts = [{"id": 1, "sport_type": "Run", "map": {"summary_polyline": "abc"}, "distance": 10_000, "moving_time": 3000,
             "max_heartrate": 180},
            {"id": 2, "sport_type": "Run", "map": {"summary_polyline": None}, "distance": 8000, "moving_time": 2400,
             "average_heartrate": 150},
            {"id": 3, "sport_type": "Ride", "map": {"summary_polyline": "x"}}]
    monkeypatch.setattr(strava, "_http", lambda *a, **k: acts)
    mapa, crudas, cinta = strava.carreras(strava.Sesion("t", "r", 9e9, 1))
    assert [c["id"] for c in mapa] == [1] and mapa[0]["fc_max"] == 180 and crudas == 3
    assert [c["id"] for c in cinta] == [2] and cinta[0]["cinta"] and cinta[0]["fc_media"] == 150


def test_sesion_estructurada_no_es_rodaje_suave():
    """«3000m (3'20"/km- 3'25"/km, 2' pausa)»: el pulso va atrasado en las series y
    queda poco tiempo en I3–I4, pero el nombre dice que es calidad."""
    z = e.zonas({"fc_max": 190})
    pocas = [0, 25 * 60, 7 * 60, 2 * 60, 0]                       # 9' en I3–I4: bajo los 12'
    serie = {"nombre": "3000m (3'20\"/km- 3'25\"/km, 2' pausa)", "tiempo_s": 34 * 60}
    assert e.tipo_sesion(serie, pocas) == "umbral"
    assert e.tipo_sesion({"nombre": "Morning Run", "tiempo_s": 34 * 60}, pocas) == "suave_rapido"
    assert e.tipo_sesion({"nombre": "Lunch Run", "tipo_entreno": 3, "tiempo_s": 2000}, pocas) == "umbral"
    # Sin detalle (sólo el promedio): con nombre de series, calidad igual.
    solo_media = [0, 40 * 60, 0, 0, 0]
    assert e.tipo_sesion({"nombre": "6x1k"}, solo_media, "fc_media") == "umbral"
    assert e.tipo_sesion({"nombre": "Rodaje"}, solo_media, "fc_media") == "suave"
    # Trotes suaves con «strides» relajados: si casi no hay I3+, sigue siendo suave.
    assert e.tipo_sesion({"nombre": "Rodaje + 6x100"}, [0, 40 * 60, 60, 0, 0]) == "suave"
    for nombre in ("2x3000", "4 x 1 km", "sub 4", "10' umbral", "fartlek", "Tempo 8k", "rec 90\""):
        assert e.estructurada({"nombre": nombre}), nombre
    assert not e.estructurada({"nombre": "Trail am por Manquehue"})
    assert z["fc"]


def test_mejores_tiempos_5k_a_maraton_y_cual_manda():
    z = e.zonas({"t5k_s": 15 * 60 + 34, "t10k_s": 32 * 60 + 20, "t21k_s": 71 * 60 + 42, "t42k_s": 2 * 3600 + 40 * 60})
    assert set(z["vdots"]) == {"5k", "10k", "21k", "42k"}
    assert z["vdot_de"] == max(z["vdots"], key=z["vdots"].get) and z["vdot"] == z["vdots"][z["vdot_de"]]
    assert z["vdots"]["42k"] < z["vdots"]["10k"]                          # el maratón flojo no manda
    assert 200 <= z["ritmo_umbral_s"] <= 215                               # ~3:25/km
    assert e.zonas({"t5k_s": 60})["ritmo"] is None                         # 0:12/km: imposible, se ignora
