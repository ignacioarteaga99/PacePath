from fastapi.testclient import TestClient
from raceengine_geodata import REGIONS

from pacepath.api import app

cli = TestClient(app)


def test_salud_e_interfaz():
    assert cli.get("/api/salud").json() == {"ok": True}
    r = cli.get("/")
    assert r.status_code == 200 and "PacePath" in r.text
    assert cli.get("/vendor/leaflet/leaflet.js").status_code == 200


def test_sesion():
    r = cli.post("/api/sesion", json={"texto": "8x400 r60"})
    assert r.json()["resumen"] == "8×400 m (pausa 60 s)"
    assert cli.post("/api/sesion", json={"texto": "nada"}).status_code == 422


def test_ruta_demo_y_gpx():
    d = REGIONS["demo"]
    body = {"lat": d.lat, "lon": d.lon, "texto": "5x400", "comuna": "demo"}
    r = cli.post("/api/ruta", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["tramo"]["semaforos"] == 0
    g = cli.post("/api/ruta.gpx", json=body)
    assert g.headers["content-type"].startswith("application/gpx+xml")
    assert "<gpx" in g.text


def test_todos_los_tipos():
    d = REGIONS["demo"]
    for texto in ("tempo 5k", "fondo 10k", "regenerativo 5k", "cuestas 6x200 4%"):
        r = cli.post("/api/ruta", json={"lat": d.lat, "lon": d.lon, "texto": texto,
                                        "comuna": "demo"})
        assert r.status_code in (200, 404), (texto, r.text)
        if r.status_code == 200:
            assert r.json()["explicacion"], texto


def test_lugares_sin_datos_no_falla():
    assert cli.get("/api/lugares", params={"q": "xx"}).json() == []


def test_trazar_a_mano():
    d = REGIONS["demo"]
    pts = [{"lat": d.lat, "lon": d.lon}, {"lat": d.lat + 0.006, "lon": d.lon},
           {"lat": d.lat + 0.006, "lon": d.lon + 0.008}]
    import pacepath.comunas as c
    original = c.comuna_para
    c.comuna_para = lambda lat, lon: "demo"
    try:
        r = cli.post("/api/trazar", json={"puntos": pts, "volver": True})
        assert r.status_code == 200, r.text
        j = r.json()
        assert len(j["partes"]) == 3                       # dos tramos y el regreso
        ini, fin = j["partes"][0]["coordenadas"][0], j["partes"][-1]["coordenadas"][-1]
        assert abs(ini[0] - fin[0]) < 1e-6 and abs(ini[1] - fin[1]) < 1e-6
        abierta = cli.post("/api/trazar", json={"puntos": pts, "volver": False}).json()
        assert len(abierta["partes"]) == 2
        assert abierta["totales"]["distancia_m"] < j["totales"]["distancia_m"]
        assert "<trkseg>" in cli.post("/api/trazar.gpx", json={"puntos": pts}).text
    finally:
        c.comuna_para = original
    assert cli.post("/api/trazar", json={"puntos": pts[:1]}).status_code == 422
