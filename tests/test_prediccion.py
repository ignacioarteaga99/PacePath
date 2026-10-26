"""Predictor del Coach."""
from datetime import date, timedelta

from pacepath import coach, prediccion
from pacepath.entreno import tiempo_equivalente, vdot

HOY = date(2026, 10, 28)


def corrida(dias_atras: int, km: float, s_km: float, carrera: bool = False) -> dict:
    d = HOY - timedelta(days=dias_atras)
    return {"id": f"{d}-{km}", "fecha": f"{d}T08:00:00", "nombre": "x", "distancia_m": km * 1000,
            "tiempo_s": km * s_km, "tipo_entreno": 1 if carrera else None}


def semanas(km_sem: float, largo: float, n: int = 10) -> list[dict]:
    out = []
    for w in range(n):
        out += [corrida(7 * w + 1, largo, 330)] + [corrida(7 * w + d, (km_sem - largo) / 4, 330) for d in (2, 3, 4, 5)]
    return out


def test_una_carrera_reciente_manda_y_estrecha_el_rango():
    runs = semanas(80, 30) + [corrida(20, 21.0975, 4380 / 21.0975, carrera=True)]       # 21K en 1:13:00
    n = prediccion.nivel(runs, {}, HOY)
    assert n["fuente"] == "carrera" and abs(n["vdot"] - vdot(21097.5, 4380)) < 0.01 and n["conf"] == 0.01
    p = prediccion.prediccion({"objetivo": "21k", "fecha": "2026-12-20"}, runs, {}, HOY, [], 1.0)
    assert p["hoy"][0] <= 4380 * 1.01 and p["hoy"][1] >= 4380 * 0.99                     # hoy ≈ la carrera
    assert p["dia"][1] < p["hoy"][1]                                                       # con el plan, mejora


def test_maraton_con_poco_volumen_suma():
    base = [corrida(20, 10, 240, carrera=True)]                                            # 10K en 40:00
    poco = prediccion.prediccion({"objetivo": "42k", "fecha": "2027-04-11"}, base + semanas(35, 18), {}, HOY, [], 1.0)
    mucho = prediccion.prediccion({"objetivo": "42k", "fecha": "2027-04-11"}, base + semanas(90, 32), {}, HOY, [], 1.0)
    assert poco["hoy"][0] > mucho["hoy"][0] and len(poco["motivos"]) >= 2
    puro = tiempo_equivalente(vdot(10_000, 2400), 42_195)
    assert abs((mucho["hoy"][0] + mucho["hoy"][1]) / 2 - puro) / puro < 0.005           # con volumen: la tabla


def test_estado_del_objetivo_y_ritmo_en_el_plan():
    runs = semanas(70, 24) + [corrida(14, 10, 240, carrera=True)]
    perfil = {"objetivo": "10k", "fecha": "2026-12-13", "dias": 5, "inicio": "2026-10-19"}
    rapido = coach.plan(perfil | {"tiempo_objetivo_s": 30 * 60}, runs, {}, hoy=date(2026, 11, 20))
    assert rapido["prediccion"]["objetivo"]["estado"] == "muy_exigente"
    lento = coach.plan(perfil | {"tiempo_objetivo_s": 50 * 60}, runs, {}, hoy=date(2026, 11, 20))
    assert lento["prediccion"]["objetivo"]["estado"] == "conservador"
    # En fase específica, la sesión a ritmo de 10K usa el ritmo objetivo (50:00 → 5:00/km).
    ses = [s for s in lento["semana"]["sesiones"] if s["tipo"] == "intensa"]
    assert ses and all(s["ritmo"] == "5:00" for s in ses)


def test_el_rango_se_angosta_con_lo_cumplido():
    runs = semanas(80, 30)
    perfil = {"objetivo": "42k", "fecha": "2026-11-01"}                     # semana de la carrera
    sin = prediccion.prediccion(perfil, runs, {}, HOY, [], None)
    con = prediccion.prediccion(perfil, runs, {}, HOY, [], 0.9, {"calidad": 8, "fondos": 3})
    poco = prediccion.prediccion(perfil, runs, {}, HOY, [], 0.3, {"calidad": 1})
    assert sin["conf_pct"] == 3.0 and con["conf_pct"] == 1.5 and poco["conf_pct"] > 3.0
    assert con["dia"][1] - con["dia"][0] < sin["dia"][1] - sin["dia"][0]
    assert con["por_que_rango"]


def test_teorica_por_umbral():
    from pacepath.entreno import ritmo_umbral_de_vdot, zonas
    assert abs(prediccion.vdot_de_umbral(ritmo_umbral_de_vdot(55)) - 55) < 0.01
    # El umbral de los datos escritos (un 10K en 40:00): la predicción teórica de 10K es ese 10K.
    te = prediccion.teorica(zonas({"t10k_s": 40 * 60}))
    assert abs(te["tiempos"]["10k"] - 2400) < 15 and "10K" in te["fuente"]
    assert te["tiempos"]["5k"] < te["tiempos"]["10k"] < te["tiempos"]["21k"] < te["tiempos"]["42k"]
    assert prediccion.teorica(zonas({})) is None                 # sin datos, nada


def test_la_revision_trae_la_teorica_sin_coach():
    from fastapi.testclient import TestClient
    from pacepath.api import app
    j = TestClient(app).post("/api/entreno/revision", json={"ajustes": {"t10k_s": 2400}, "carreras": semanas(60, 20),
                                                           "hoy": HOY.isoformat()}).json()
    assert "coach" not in j and set(j["teorica"]["tiempos"]) == {"5k", "10k", "21k", "42k"}
