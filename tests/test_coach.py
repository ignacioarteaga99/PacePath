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
