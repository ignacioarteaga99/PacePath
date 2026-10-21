"""Dónde corriste: ciudad y país sin servicios externos (GeoNames)."""
from fastapi.testclient import TestClient

from pacepath.api import app
from pacepath.ciudades import nombrar


def _cpc(r):
    return {k: r[k] for k in ("ciudad", "pais", "cerca")} if r else r


def test_la_ciudad_importante_cerca_no_la_comuna():
    assert _cpc(nombrar(-33.4258, -70.61)) == {"ciudad": "Santiago", "pais": "CL", "cerca": False}   # Providencia
    assert nombrar(-33.35, -70.52)["ciudad"] == "Santiago"                                    # Lo Barnechea
    assert nombrar(-33.02, -71.55)["ciudad"] == "Viña del Mar"
    assert _cpc(nombrar(40.68, -73.95)) == {"ciudad": "New York City", "pais": "US", "cerca": False}
    assert nombrar(40.415, -3.684)["pais"] == "ES"


def test_lejos_de_todo():
    assert nombrar(-50.94, -73.4)["cerca"] is True       # Torres del Paine: «cerca de …»
    assert nombrar(-30.0, -100.0) is None                 # en el océano


def test_api_donde():
    r = TestClient(app).post("/api/donde", json={"puntos": [[-33.4258, -70.61], [-30, -100]]})
    assert r.status_code == 200
    assert [_cpc(x) for x in r.json()] == [{"ciudad": "Santiago", "pais": "CL", "cerca": False}, None]


def test_continente_y_antartica():
    assert nombrar(-33.4258, -70.61)["continente"] == "SA"
    assert nombrar(40.415, -3.684)["continente"] == "EU"
    a = nombrar(-77.85, 166.67)                      # McMurdo: sin ciudades, pero cuenta
    assert a["continente"] == "AN" and a["pais"] == "AQ" and a["ciudad"] is None


def test_barrio_con_limites_de_osm(monkeypatch):
    import numpy as np
    from pacepath import barrios
    caja = [-33.44, -70.62, -33.41, -70.59]
    anillo = np.array([[-33.44, -70.62], [-33.44, -70.59], [-33.41, -70.59], [-33.41, -70.62], [-33.44, -70.62]])
    monkeypatch.setattr(barrios, "_datos", lambda: [("Providencia", "CL", caja, [anillo])])
    assert nombrar(-33.4258, -70.61)["barrio"] == "Providencia"
    # Fuera de los límites conocidos: el lugar de GeoNames más cercano.
    assert nombrar(-33.456, -70.598)["barrio"] == "Ñuñoa"
