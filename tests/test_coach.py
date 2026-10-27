"""PacePath Coach: índice de esfuerzo, carga sana, plan y recálculo."""
from datetime import date, timedelta

from pacepath import coach
from pacepath.entreno import zonas

AJ = {"t10k_s": 45 * 60}                     # umbral ~4:37/km
Z = zonas(AJ)
HOY = date(2026, 10, 28)                     # miércoles


def carrera(d: date, km: float, s_km: float = 330, nombre: str = "Rodaje") -> dict:
    return {"id": f"{d}-{km}", "fecha": f"{d}T07:00:00", "fecha_local": f"{d}T07:00:00", "nombre": nombre,
            "distancia_m": km * 1000, "tiempo_s": km * s_km}


def historial(semanas: int, km_dia: float = 8, dias=(0, 2, 4, 6), hasta: date = HOY) -> list[dict]:
    lunes = hasta - timedelta(days=hasta.weekday())
    out = []
    for w in range(semanas, 0, -1):
        for d in dias:
            out.append(carrera(lunes - timedelta(weeks=w) + timedelta(days=d), km_dia))
    return out


def test_iep_una_hora_a_umbral_vale_100():
    c = carrera(HOY, 3600 / 290, 290)                     # 1 h en I3 (307–277 s/km)
    assert 95 <= coach.iep(c, Z) <= 105
    suave = carrera(HOY, 10, 360)
    assert coach.iep(suave, Z) < coach.iep(c, Z)


def test_carga_sana_y_riesgo():
    estable = historial(10)
    assert coach.estado_carga(estable, Z, HOY)["nivel"] == "verde"
    pico = estable + [carrera(HOY - timedelta(days=i), 18, 300) for i in range(6)]   # una semana desbordada
    e = coach.estado_carga(pico, Z, HOY)
    assert e["nivel"] in ("rojo", "ambar") and e["acwr"] > 1.3


def test_plan_hacia_un_10k():
    runs = historial(8)
    p = coach.plan({"objetivo": "10k", "fecha": "2026-12-20", "dias": 4, "inicio": HOY.isoformat()}, runs, AJ, hoy=HOY)
    fases = [w["fase"] for w in p["semanas"]]
    assert fases[0] == "basica" and "carga" in p and p["carga"]["nivel"] in ("verde", "azul")
    km_real = 32
    assert p["semana"]["km"] <= km_real * coach.SUBIDA_MAX + 1          # nunca más de +10 % sobre lo corrido
    sem = p["semana"]["sesiones"]
    assert {s["tipo"] for s in sem} >= {"umbral", "fondo"} and all(s.get("ritmo") for s in sem)
    assert all(s["texto"] for s in sem if s["tipo"] != "extra")     # PacePath sabe armar cada una
    # La última semana tiene la carrera, el día de la carrera.
    todo = coach.plan({"objetivo": "10k", "fecha": "2026-12-20", "dias": 4, "inicio": HOY.isoformat()}, runs, AJ,
                      hoy=date(2026, 12, 15))
    assert todo["semana"]["fase"] == "competencia"
    assert any(s["tipo"] == "carrera" and s["fecha"] == "2026-12-20" for s in todo["semana"]["sesiones"])


def test_calidad_saltada_se_mueve():
    runs = historial(8)
    perfil = {"objetivo": "10k", "fecha": "2026-12-20", "dias": 4, "dia_largo": 6, "inicio": "2026-10-19"}
    p = coach.plan(perfil, runs, AJ, hoy=HOY)          # martes 27: calidad sin correr; el jueves está libre
    martes = next(s for s in p["semana"]["sesiones"] if s.get("movida_a") or s.get("movida"))
    assert martes and any("sugiero" in m for m in p["mensajes"])


def test_carga_en_rojo_cambia_calidad_por_suave():
    runs = historial(8) + [carrera(HOY - timedelta(days=i), 20, 290) for i in range(1, 7)]
    p = coach.plan({"objetivo": "10k", "fecha": "2026-12-20", "dias": 5, "inicio": HOY.isoformat()}, runs, AJ, hoy=HOY)
    assert p["carga"]["nivel"] == "rojo"
    futuras = [s for s in p["semana"]["sesiones"] if s["estado"] in ("hoy", "pendiente")]
    assert not any(s["tipo"] in ("umbral", "intensa") for s in futuras)


def test_sin_fecha_de_carrera_pide_la_fecha():
    assert "error" in coach.plan({"objetivo": "21k"}, [], AJ, hoy=HOY)
    assert coach.plan({"objetivo": "forma"}, [], AJ, hoy=HOY)["semana"] is not None


def test_con_coach_la_revision_usa_su_fase():
    from fastapi.testclient import TestClient
    from pacepath.api import app
    cli = TestClient(app)
    runs = historial(8)
    cuerpo = {"ajustes": {**AJ, "fase": "basica"}, "carreras": runs, "hoy": "2026-12-08",
              "coach": {"objetivo": "10k", "fecha": "2026-12-20", "dias": 4, "inicio": "2026-10-19"}}
    j = cli.post("/api/entreno/revision", json=cuerpo).json()
    assert j["coach"]["semana"]["fase"] == "competencia" == j["revision"]["fase"]
    sin = cli.post("/api/entreno/revision", json={**cuerpo, "coach": None}).json()
    assert "coach" not in sin and sin["revision"]["fase"] == "basica"


def test_siete_dias_y_dobles():
    runs = historial(8, km_dia=14, dias=range(7))
    perfil = {"objetivo": "42k", "fecha": "2027-04-11", "dias": 7, "dobles": 3, "inicio": HOY.isoformat()}
    p = coach.plan(perfil, runs, AJ, hoy=HOY)
    ses = [s for s in p["semana"]["sesiones"] if s["tipo"] != "extra"]
    dobles = [s for s in ses if s.get("turno") == 2]
    assert len({s["fecha"] for s in ses}) == 7 and len(dobles) == 3
    fondo = next(s for s in ses if s["tipo"] == "fondo")
    assert fondo["fecha"] not in {d["fecha"] for d in dobles}                     # nunca el día del fondo
    calidad = {s["fecha"] for s in ses if s["tipo"] in ("umbral", "intensa")}
    assert calidad <= {d["fecha"] for d in dobles}                                 # primero en los días de calidad
    assert all(d["km"] <= coach.DOBLE_MAX_KM for d in dobles)


def test_doble_umbral():
    runs = historial(8, km_dia=14, dias=range(7))
    base = {"objetivo": "42k", "fecha": "2027-04-11", "dias": 6, "dobles": 2, "inicio": HOY.isoformat()}
    p = coach.plan(base | {"doble_umbral": True}, runs, AJ, hoy=HOY)
    ses = p["semana"]["sesiones"]
    tardes = [s for s in ses if s.get("turno") == 2 and s["tipo"] == "umbral"]
    assert tardes and all(any(m["fecha"] == t["fecha"] and m["tipo"] == "umbral" and m.get("turno", 1) == 1 for m in ses)
                          for t in tardes)
    # Con 4 días no aplica (el formulario tampoco lo deja).
    p4 = coach.plan(base | {"dias": 4, "doble_umbral": True}, runs, AJ, hoy=HOY)
    assert not any(s.get("turno") == 2 and s["tipo"] == "umbral" for s in p4["semana"]["sesiones"])


# ── Días elegidos, mover, pausas, sensaciones y el porqué ──────────────────

BASE = {"objetivo": "10k", "fecha": "2026-12-20", "inicio": "2026-10-19"}


def _ses(p):
    return [s for s in p["semana"]["sesiones"] if s["tipo"] != "extra"]


def test_dias_elegidos():
    runs = historial(8)
    # martes, jueves, sábado y domingo: el fondo el domingo y las calidades separadas de él.
    p = coach.plan(BASE | {"dias_semana": [1, 3, 5, 6], "dia_largo": 6}, runs, AJ, hoy=HOY)
    dias = {date.fromisoformat(s["fecha"]).weekday(): s["tipo"] for s in _ses(p) if s.get("turno", 1) == 1}
    assert set(dias) == {1, 3, 5, 6} and dias[6] == "fondo" and dias[5] == "suave"
    # Si el día del fondo no está entre los elegidos, va al último del fin de semana.
    plantilla, largo = coach._plantilla({"dias_semana": [0, 2, 4, 5], "dia_largo": 6})
    assert largo == 5 and plantilla[0] == "f"
    # Las dos calidades nunca quedan pegadas al fondo si hay cómo evitarlo.
    plantilla, _ = coach._plantilla({"dias_semana": [0, 1, 2, 3, 4, 6], "dia_largo": 6})
    q = [r for r, t in plantilla.items() if t in ("q", "q2")]
    assert all(r not in (-1, -6) for r in q) and abs(q[0] - q[1]) > 1


def test_mover_sesion_sin_juntar_dias_duros():
    runs = historial(8)
    hoy = date(2026, 10, 27)                                  # martes, día de calidad
    perfil = BASE | {"dias": 5, "dia_largo": 6}
    antes = {s["fecha"]: s["tipo"] for s in _ses(coach.plan(perfil, runs, AJ, hoy=hoy)) if s.get("turno", 1) == 1}
    assert antes["2026-10-27"] == "umbral"
    p = coach.plan(perfil | {"movidas": [{"de": "2026-10-27", "a": "2026-10-28"}]}, runs, AJ, hoy=hoy)
    ses = {s["fecha"]: s for s in _ses(p) if s.get("turno", 1) == 1}
    assert "2026-10-27" not in ses and ses["2026-10-28"]["tipo"] == "umbral"
    assert ses["2026-10-28"]["movida_por_ti"] == "2026-10-27"
    duros = sorted(date.fromisoformat(f) for f, s in ses.items() if s["tipo"] in coach.DUROS)
    assert all((b - a).days > 1 for a, b in zip(duros, duros[1:]))     # sin dos días duros seguidos
    assert p["hoy"] is None                                            # hoy ya no hay sesión
    # A un día que ya tenía calidad: la de ese día busca otro sin quedar pegada (o se cae).
    p = coach.plan(perfil | {"movidas": [{"de": "2026-10-27", "a": "2026-10-29"}]}, runs, AJ, hoy=hoy)
    duros = sorted({date.fromisoformat(s["fecha"]) for s in _ses(p) if s.get("turno", 1) == 1 and s["tipo"] in coach.DUROS})
    assert all((b - a).days > 1 for a, b in zip(duros, duros[1:]))
    # Al día del fondo: el fondo se queda y la calidad no comparte día con él.
    p = coach.plan(perfil | {"movidas": [{"de": "2026-10-27", "a": "2026-11-01"}]}, runs, AJ, hoy=hoy)
    domingo = [s for s in _ses(p) if s["fecha"] == "2026-11-01" and s.get("turno", 1) == 1]
    assert [s["tipo"] for s in domingo] == ["fondo"]


def test_pausa_y_reentrada():
    runs = historial(8, km_dia=10)
    perfil = BASE | {"dias": 5}
    normal = coach.plan(perfil, runs, AJ, hoy=HOY)
    # En pausa desde el lunes: hoy no hay sesión y nada de la pausa queda como «no se hizo».
    p = coach.plan(perfil | {"pausas": [{"desde": "2026-10-26", "motivo": "enfermedad"}]}, runs, AJ, hoy=HOY)
    assert p["pausa"]["dias"] == 3 and p["hoy"] is None
    assert not any(s["estado"] == "saltada" and s["fecha"] >= "2026-10-26" for s in _ses(p))
    assert "pausa" in p["mensajes"][0]
    # Dos semanas de lesión: vuelve sólo suave, menos volumen y avisa.
    hoy = date(2026, 11, 9)
    p = coach.plan(perfil | {"pausas": [{"desde": "2026-10-24", "hasta": "2026-11-08", "motivo": "lesion"}]},
                   runs, AJ, hoy=hoy)
    assert p["pausa"] is None and "16 días" in p["mensajes"][0]
    assert not any(s["tipo"] in ("umbral", "intensa") for s in _ses(p))
    assert p["semana"]["km"] < 0.8 * normal["semana"]["meta_km"]
    assert all("sin correr" in s["porque"] for s in _ses(p))


def test_sensaciones():
    runs = historial(8)
    perfil = BASE | {"dias": 5}
    p = coach.plan(perfil, runs + [carrera(HOY - timedelta(days=1), 8)], AJ, hoy=HOY)
    assert p["preguntar"]["fecha"] == "2026-10-27"
    # Dolor: la calidad de los próximos días pasa a suave.
    dolor = [{"fecha": "2026-10-27", "nota": 3, "dolor": "rodilla"}]
    p = coach.plan(perfil | {"sensaciones": dolor}, runs, AJ, hoy=HOY)
    proximas = [s for s in _ses(p) if "2026-10-28" <= s["fecha"] <= "2026-10-31"]
    assert not any(s["tipo"] in ("umbral", "intensa") for s in proximas)
    assert any("rodilla" in m for m in p["mensajes"])
    # Dolor repetido: hoy, descanso.
    p = coach.plan(perfil | {"sensaciones": dolor + [{"fecha": "2026-10-25", "nota": 2, "dolor": "rodilla"}]},
                   runs, AJ, hoy=HOY)
    assert p["descanso"] and p["hoy"] is None
    # Dos salidas malas seguidas: la próxima calidad pasa a suave.
    malas = [{"fecha": "2026-10-26", "nota": 2}, {"fecha": "2026-10-27", "nota": 1}]
    normal = coach.plan(perfil, runs, AJ, hoy=HOY)
    p = coach.plan(perfil | {"sensaciones": malas}, runs, AJ, hoy=HOY)
    cal = lambda x: sum(s["tipo"] in ("umbral", "intensa") and s["estado"] in ("hoy", "pendiente") for s in _ses(x))
    assert cal(p) == cal(normal) - 1 and any("mal" in m for m in p["mensajes"])


def test_cada_sesion_dice_por_que():
    runs = historial(8)
    p = coach.plan(BASE | {"dias": 6, "dobles": 1}, runs, AJ, hoy=HOY)
    assert all(s.get("porque") for s in _ses(p))
    fondo = next(s for s in _ses(p) if s["tipo"] == "fondo")
    assert "km" in fondo["porque"]


def test_api_acepta_lo_nuevo():
    from fastapi.testclient import TestClient
    from pacepath.api import app
    cli = TestClient(app)
    cuerpo = {"ajustes": AJ, "carreras": historial(8), "hoy": HOY.isoformat(),
              "coach": BASE | {"dias": 4, "dias_semana": [1, 3, 5, 6],
                               "sensaciones": [{"fecha": "2026-10-27", "nota": 4}],
                               "pausas": [{"desde": "2026-10-01", "hasta": "2026-10-03", "motivo": "viaje"}],
                               "movidas": [{"de": "2026-10-29", "a": "2026-10-30"}]}}
    r = cli.post("/api/entreno/revision", json=cuerpo)
    assert r.status_code == 200 and r.json()["coach"]["semana"]
    malo = {**cuerpo, "coach": {**cuerpo["coach"], "sensaciones": [{"fecha": "2026-10-27", "nota": 9}]}}
    assert cli.post("/api/entreno/revision", json=malo).status_code == 422
