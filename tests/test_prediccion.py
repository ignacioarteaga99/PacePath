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
