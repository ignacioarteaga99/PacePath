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


def test_tipos_aun_no_disponibles():
    d = REGIONS["demo"]
    r = cli.post("/api/ruta", json={"lat": d.lat, "lon": d.lon, "texto": "fondo 18k",
                                    "comuna": "demo"})
    assert r.status_code == 501
