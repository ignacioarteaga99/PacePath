"""Lo de la beta: métricas anónimas, mensaje fuera del mapa, tope de Strava."""
import io
import urllib.error

import pytest
from fastapi.testclient import TestClient

from pacepath import strava, uso
from pacepath.api import app

cli = TestClient(app)


def test_metricas_protegidas_y_anonimas(monkeypatch):
    monkeypatch.delenv("PACEPATH_ADMIN", raising=False)
    assert cli.get("/api/uso").status_code == 404                     # sin clave configurada: no existe
    monkeypatch.setenv("PACEPATH_ADMIN", "clave-larga")
    assert cli.get("/api/uso", params={"clave": "otra"}).status_code == 404
    cli.post("/api/evento", json={"tipo": "comentario", "detalle": "series"})
    cli.post("/api/evento", json={"tipo": "cualquier-cosa"})            # tipos desconocidos: se ignoran
    r = cli.get("/api/uso", params={"clave": "clave-larga"}).json()
    assert r["totales"]["comentario"] >= 1 and "cualquier-cosa" not in r["totales"]


def test_fuera_del_mapa_dice_donde_hay_y_cuenta_la_comuna(monkeypatch):
    antes = sum(n for k, n in uso._CUENTA.items() if k[1] == "fuera_de_mapa")
    r = cli.post("/api/ruta", json={"lat": -33.02, "lon": -71.55, "texto": "6x1k"})   # Viña del Mar
    assert r.status_code == 422 and "Todavía no hay mapa ahí" in r.json()["detail"]
    claves = [k for k in uso._CUENTA if k[1] == "fuera_de_mapa"]
    assert sum(uso._CUENTA[k] for k in claves) == antes + 1
    assert all(isinstance(x, str) and "." not in x for k in claves for x in k[2:])    # nombres, no coordenadas


def test_tope_de_atletas_de_strava(monkeypatch):
    def tope(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                     io.BytesIO(b'{"message":"Limit of connected athletes exceeded"}'))
    monkeypatch.setattr(strava.urllib.request, "urlopen", tope)
    with pytest.raises(strava.StravaLimite):
        strava._http("POST", strava.TOKEN, {"code": "x"})


def test_con_dominio_propio_onrender_redirige_las_paginas(monkeypatch):
    c = TestClient(app, base_url="https://pacepath.onrender.com")
    monkeypatch.setenv("PACEPATH_URL", "https://pacepath.onrender.com")       # sin dominio propio: nada cambia
    assert c.get("/", follow_redirects=False).status_code == 200
    monkeypatch.setenv("PACEPATH_URL", "https://pacepath.com")
    r = c.get("/privacidad?lang=en", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "https://pacepath.com/privacidad?lang=en"
    assert c.get("/api/salud").status_code == 200                              # la API no se mueve
    assert TestClient(app, base_url="https://pacepath.com").get("/", follow_redirects=False).status_code == 200


def test_clave_de_mapas_de_esri_va_en_la_pagina_solo_si_existe(monkeypatch):
    c = TestClient(app)
    monkeypatch.delenv("ESRI_API_KEY", raising=False)
    assert "window.PP_ESRI =" not in c.get("/").text
    monkeypatch.setenv("ESRI_API_KEY", "AAPTclave123")
    assert '<script>window.PP_ESRI = "AAPTclave123";</script>' in c.get("/").text
    monkeypatch.setenv("ESRI_API_KEY", 'x"</script><b>')                        # mal pegada: no rompe la página
    html = c.get("/").text
    assert "</script><b>" not in html and 'window.PP_ESRI = "x\\"\\u003c/script>\\u003cb>";' in html
