"""Flujo de Strava con la API simulada (sin red)."""
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from pacepath import strava
from pacepath.api import app


@pytest.fixture
def falso(monkeypatch):
    monkeypatch.setenv("STRAVA_CLIENT_ID", "289395")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secreto-de-prueba")
    llamadas = []

    def http(metodo, url, datos=None, token=None):
        llamadas.append((metodo, url.split("?")[0], datos, token))
        if url == strava.TOKEN and datos["grant_type"] == "authorization_code":
            assert datos["code"] == "codigo-ok"
            return {"access_token": "tok-acceso-en-claro", "refresh_token": "r1", "expires_at": 0,
                    "athlete": {"id": 7, "firstname": "Ignacio"}}
        if url == strava.TOKEN and datos["grant_type"] == "refresh_token":
            assert datos["refresh_token"] == "r1"
            return {"access_token": "a2", "refresh_token": "r2",
                    "expires_at": int(time.time()) + 21600}
        if url.startswith(strava.API + "/athlete/activities"):
            assert token == "a2"
            return [
                {"id": 1, "name": "Fondo", "sport_type": "Run", "distance": 18000.4,
                 "start_date": "2026-10-19T10:00:00Z", "map": {"summary_polyline": "abc"}},
                {"id": 2, "name": "Bici", "sport_type": "Ride", "distance": 40000,
                 "map": {"summary_polyline": "def"}},
                {"id": 3, "name": "Cinta", "sport_type": "VirtualRun", "distance": 5000,
                 "map": {"summary_polyline": ""}},
            ]
        if url == strava.REVOCAR:
            return {}
        if url == strava.API + "/athlete":
            return {"id": 7, "firstname": "Ignacio", "lastname": "A.", "profile_medium": "https://f/x.jpg",
                    "city": "Santiago", "country": "Chile", "created_at": "2015-03-01T00:00:00Z"}
        if url == strava.API + "/athletes/7/stats":
            t = {"count": 12, "distance": 123456.0, "moving_time": 36000, "elevation_gain": 812.4}
            return {"recent_run_totals": t, "ytd_run_totals": t, "all_run_totals": t}
        raise AssertionError(url)

    monkeypatch.setattr(strava, "_http", http)
    return llamadas


def test_flujo_completo(falso):
    cli = TestClient(app, base_url="https://pacepath.onrender.com")
    assert cli.get("/api/strava/estado").json()["conectado"] is False

    r = cli.get("/api/strava/conectar", follow_redirects=False)
    assert r.status_code in (302, 307)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["client_id"] == ["289395"]
    assert q["redirect_uri"] == ["https://pacepath.onrender.com/api/strava/vuelta"]
    assert q["scope"] == [strava.ALCANCE]
    estado = q["state"][0]

    # Un «state» distinto (CSRF) no conecta.
    r = cli.get("/api/strava/vuelta", params={"code": "codigo-ok", "state": "otro",
                                              "scope": "read,activity:read_all"},
                follow_redirects=False)
    assert r.headers["location"].endswith("#ciudad-error")

    r = cli.get("/api/strava/vuelta", params={"code": "codigo-ok", "state": estado,
                                              "scope": "read,activity:read_all"},
                follow_redirects=False)
    assert r.headers["location"] == "/#ciudad"
    cookie = cli.cookies.get(strava.COOKIE)
    assert cookie and "tok-acceso-en-claro" not in cookie                  # cifrada, no en claro
    e = cli.get("/api/strava/estado").json()
    assert e | {"vuelta": None} == {"configurado": True, "conectado": True, "nombre": "Ignacio",
                                    "privadas": True, "vuelta": None}
    assert e["vuelta"] == "https://pacepath.onrender.com/api/strava/vuelta"

    # El token venció (expires_at=0): se renueva solo y sólo vuelven carreras.
    j = cli.get("/api/strava/actividades").json()
    assert [c["id"] for c in j["carreras"]] == [1]
    assert j["carreras"][0]["distancia_m"] == 18000 and j["ultima"] is True
    assert strava.Sesion.descifrar(cli.cookies.get(strava.COOKIE)).access_token == "a2"

    pf = cli.get("/api/strava/perfil").json()
    assert pf["nombre"] == "Ignacio" and pf["ciudad"] == "Santiago, Chile" and pf["desde"] == "2015-03-01"
    assert pf["historico"] == {"carreras": 12, "km": 123.5, "horas": 10.0, "desnivel_m": 812}
    c0 = j["carreras"][0]
    assert {"tiempo_s", "desnivel_m", "fecha_local", "fc_media", "tipo_entreno"} <= set(c0)

    assert cli.post("/api/strava/desconectar").json() == {"ok": True}
    assert any(u == strava.REVOCAR for _, u, _, _ in falso)
    assert cli.get("/api/strava/estado").json()["conectado"] is False


def test_cancelado_o_sin_permiso(falso):
    cli = TestClient(app, base_url="https://pacepath.onrender.com")
    r = cli.get("/api/strava/vuelta", params={"error": "access_denied"}, follow_redirects=False)
    assert r.headers["location"].endswith("#ciudad-cancelado")
    q = parse_qs(urlparse(cli.get("/api/strava/conectar", follow_redirects=False)
                          .headers["location"]).query)
    r = cli.get("/api/strava/vuelta", params={"code": "codigo-ok", "state": q["state"][0],
                                              "scope": "read"}, follow_redirects=False)
    assert r.headers["location"].endswith("#ciudad-sin-permiso")


def test_sin_cookie_y_cookie_falsa(falso):
    cli = TestClient(app)
    assert cli.get("/api/strava/actividades").status_code == 401
    cli.cookies.set(strava.COOKIE, "basura")
    assert cli.get("/api/strava/actividades").status_code == 401


def test_foto_del_atleta(monkeypatch):
    """La foto sale de la API de Strava (no del teléfono) y sólo si es https e imagen."""
    perfil_url = {"v": "https://dgalywyr863hv.cloudfront.net/pictures/athletes/7/large.jpg"}
    monkeypatch.setattr(strava, "_http", lambda m, url, datos=None, token=None: {"id": 7, "profile": perfil_url["v"]})
    pedidas = []

    class Resp:
        headers = type("H", (), {"get_content_type": staticmethod(lambda: "image/jpeg")})()
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return b"\xff\xd8jpeg"

    def abrir(req, timeout=None):
        pedidas.append(req.full_url)
        return Resp()
    monkeypatch.setattr(strava.urllib.request, "urlopen", abrir)
    s = strava.Sesion("a", "r", 0, 7)
    assert strava.foto(s) == (b"\xff\xd8jpeg", "image/jpeg")
    assert pedidas == [perfil_url["v"]]
    perfil_url["v"] = "avatar/athlete/large.png"            # sin foto: el dibujo genérico de Strava
    assert strava.foto(s) is None
    perfil_url["v"] = "http://inseguro/x.jpg"
    assert strava.foto(s) is None and len(pedidas) == 1
