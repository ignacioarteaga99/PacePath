"""Bitácora: de las vueltas de Strava a las series."""
from fastapi.testclient import TestClient

from pacepath import bitacora
from pacepath.api import app


def v(d, t):
    return {"distancia_m": d, "tiempo_s": t}


# 2x3x1000 con 1' entre reps y 2' entre series, con lap en cada rep y en cada pausa.
SESION = [v(3000, 900),                                        # calentamiento
          v(1000, 195), v(180, 60), v(1000, 194), v(180, 60), v(1000, 192),
          v(350, 120),                                         # pausa entre series
          v(1000, 198), v(180, 60), v(1000, 202), v(180, 60), v(1000, 188),
          v(2000, 640)]                                        # vuelta a la calma


def test_2x3x1000_sin_plan():
    p = bitacora.proponer(SESION)
    b = p["bloques"][0]
    assert (b["series"], b["reps"], b["dist_m"]) == (2, 3, 1000)
    assert b["tiempos"] == [[195, 194, 192], [198, 202, 188]]
    assert (b["p_rep"], b["p_serie"]) == (60, 120)
    assert p["calentamiento_km"] == 3.0 and p["vuelta_calma_km"] == 2.0
    assert p["marcas"][0] == "cal" and p["marcas"][-1] == "vc" and p["marcas"][2] == "pausa"


def test_gps_imperfecto_redondea_y_con_plan_usa_su_distancia():
    gps = [v(2500, 760)] + [x for d, t in [(1013, 196), (996, 193), (1021, 195), (987, 191)] for x in (v(d, t), v(150, 90))] + [v(1500, 500)]
    sin = bitacora.proponer(gps)["bloques"][0]
    assert (sin["series"], sin["reps"], sin["dist_m"], sin["p_rep"]) == (1, 4, 1000, 90)
    con = bitacora.proponer(gps, plan={"series": 1, "reps": 4, "dist_m": 1000, "obj": [190, 200]})["bloques"][0]
    assert con["obj"] == [190, 200] and con["tiempos"] == [[196, 193, 195, 191]]


def test_por_tiempo_6x3min():
    laps = [v(2000, 640)] + [x for d in (880, 905, 870, 910, 890, 860) for x in (v(d, 180), v(200, 90))] + [v(1000, 330)]
    b = bitacora.proponer(laps)["bloques"][0]
    assert b["dur_s"] == 180 and b["dist_m"] is None and b["tiempos"][0][:2] == [880, 905]


def test_sin_series_y_marcas_a_mano():
    rodaje = [v(1000, 330), v(1000, 328), v(1000, 335), v(1000, 331)]
    assert bitacora.proponer(rodaje)["bloques"] == []
    # El corredor marca a mano cuáles son repeticiones.
    p = bitacora.proponer(SESION, marcas=["cal", "rep", "pausa", "rep", "pausa", "rep", "pausa", "rep", "pausa", "rep", "pausa", "rep", "vc"])
    assert p["bloques"][0]["series"] * p["bloques"][0]["reps"] == 6 and p["marcas"][6] == "pausa"


def lap(d, t):
    return {"distancia_m": d, "tiempo_s": t}


def test_vueltas_reales_10x1000():
    # 10x1000m (1' pausa) de verdad: reps de 968–997 m por GPS, pausas paradas de ~60 s y 40–75 m, sin calentamiento con lap.
    reps = [(987.3, 210), (968.6, 211), (988.2, 209), (996.7, 208), (991.7, 208), (991.5, 207), (991.2, 206), (977.9, 204), (990.2, 205), (981.1, 205)]
    pausas = [(74, 60), (55.8, 61), (41.7, 61), (59.7, 61), (45.9, 58), (52.2, 64), (47.1, 74), (71.9, 62), (61.5, 66)]
    laps = [x for k, r in enumerate(reps) for x in ([lap(*r)] + ([lap(*pausas[k])] if k < len(pausas) else []))] + [lap(126, 121)]
    b = bitacora.proponer(laps)["bloques"]
    assert len(b) == 1 and (b[0]["series"], b[0]["reps"], b[0]["dist_m"], b[0]["p_rep"]) == (1, 10, 1000, 60)


def test_vueltas_reales_3x2000_mas_5x400():
    # 3x2000m (2' pausa) + 5x400m (1' pausa): dos bloques.
    laps = [lap(2001.7, 399), lap(175.4, 123), lap(2003.4, 414), lap(319.2, 130), lap(2002.8, 399), lap(431.8, 148),
            lap(406.8, 72), lap(133.9, 59), lap(401.2, 72), lap(73.1, 60), lap(400.7, 71), lap(97, 62), lap(398.5, 70),
            lap(73.9, 62), lap(400.9, 67), lap(29.1, 6)]
    b = bitacora.proponer(laps)["bloques"]
    assert [(x["series"], x["reps"], x["dist_m"]) for x in b] == [(1, 3, 2000), (1, 5, 400)]
    assert b[0]["p_rep"] in (125, 130) and b[1]["p_rep"] == 60
    assert b[1]["tiempos"] == [[72, 72, 71, 70, 67]]


def test_api():
    cli = TestClient(app)
    r = cli.post("/api/bitacora/series", json={"vueltas": SESION})
    assert r.status_code == 200 and r.json()["bloques"][0]["reps"] == 3
    assert cli.post("/api/bitacora/series", json={"vueltas": SESION, "marcas": ["x"] * 13}).status_code == 422
    assert cli.get("/api/strava/vueltas?id=1").status_code == 401
