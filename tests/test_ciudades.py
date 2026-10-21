"""Dónde corriste: ciudad y país sin servicios externos (GeoNames)."""
from fastapi.testclient import TestClient

from pacepath.api import app
from pacepath.ciudades import nombrar


def test_la_ciudad_importante_cerca_no_la_comuna():
    assert nombrar(-33.4258, -70.61) == {"ciudad": "Santiago", "pais": "CL", "cerca": False}   # Providencia
    assert nombrar(-33.35, -70.52)["ciudad"] == "Santiago"                                    # Lo Barnechea
    assert nombrar(-33.02, -71.55)["ciudad"] == "Viña del Mar"
    assert nombrar(40.68, -73.95) == {"ciudad": "New York City", "pais": "US", "cerca": False}
    assert nombrar(40.415, -3.684)["pais"] == "ES"


def test_lejos_de_todo():
    assert nombrar(-50.94, -73.4)["cerca"] is True       # Torres del Paine: «cerca de …»
    assert nombrar(-30.0, -100.0) is None                 # en el océano


def test_api_donde():
    r = TestClient(app).post("/api/donde", json={"puntos": [[-33.4258, -70.61], [-30, -100]]})
    assert r.status_code == 200
    assert r.json() == [{"ciudad": "Santiago", "pais": "CL", "cerca": False}, None]
