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


def test_mapa_propio_su_direccion_y_su_estilo(monkeypatch):
    c = TestClient(app)
    monkeypatch.delenv("PACEPATH_MAPA_URL", raising=False)
    assert "window.PP_MAPA =" not in c.get("/").text                            # sin mapa propio: Esri
    monkeypatch.setenv("PACEPATH_MAPA_URL", "https://mapas.pacepathapp.com/")
    assert 'window.PP_MAPA = "https://mapas.pacepathapp.com";' in c.get("/").text
    assert "estiloPacePath" in c.get("/mapa/estilo.js").text
    assert c.get("/mapa/mini-dia.png").status_code == 200
    assert c.get("/vendor/mapa/maplibre-gl.js").status_code == 200


def test_guia_beta():
    r = TestClient(app).get("/beta")
    assert r.status_code == 200 and "Enviar comentario" in r.text and "Agregar a inicio" in r.text


def test_trafico_comprimido_y_guardado_en_el_telefono():
    c = TestClient(app)
    r = c.get("/", headers={"Accept-Encoding": "gzip"})
    assert r.headers["content-encoding"] == "gzip" and r.headers["etag"]
    assert c.get("/", headers={"If-None-Match": r.headers["etag"]}).status_code == 304        # sin cambios: sin cuerpo
    assert "max-age=604800" in c.get("/vendor/mapa/pmtiles.js").headers["cache-control"]
    assert "max-age=3600" in c.get("/mapa/estilo.js").headers["cache-control"]


def test_freno_al_cupo_de_strava(monkeypatch):
    """Strava informa el cupo de toda la app en cada respuesta: al 90 % se frena antes de llamarlo."""
    monkeypatch.setattr(strava, "_cupo", dict(strava._cupo, ventana=None, dia=None))
    llamadas = []

    class Resp(io.BytesIO):
        def __init__(self, uso):
            super().__init__(b"[]")
            self.headers = {"X-ReadRateLimit-Usage": uso, "X-ReadRateLimit-Limit": "200,2000"}
        def __enter__(self): return self
        def __exit__(self, *a): pass

    uso = {"v": "50,300"}
    def abrir(req, timeout=None):
        llamadas.append(req.full_url)
        return Resp(uso["v"])
    monkeypatch.setattr(strava.urllib.request, "urlopen", abrir)
    s = strava.Sesion(access_token="a", refresh_token="r", expires_at=4_000_000_000, atleta_id=1)
    strava.carreras(s, 1)                                   # 50 de 200: sigue
    uso["v"] = "181,300"
    strava.carreras(s, 2)                                   # llega al 90 % de los 15 minutos
    with pytest.raises(strava.StravaEspera, match="Vuelve en"):
        strava.carreras(s, 3)
    assert len(llamadas) == 2                               # la tercera ni siquiera llamó a Strava
    monkeypatch.setattr(strava, "_cupo", dict(strava._cupo, ventana=None, uso15=0, usodia=1850))
    with pytest.raises(strava.StravaEspera, match="mañana"):
        strava.carreras(s, 1)                               # 92 % del día: hasta mañana
    strava._http("POST", strava.TOKEN, {"x": 1})             # conectar/renovar no se frena (no es lectura)


def test_boton_de_aporte_solo_con_link(monkeypatch):
    c = TestClient(app)
    monkeypatch.delenv("PACEPATH_APORTE_URL", raising=False)
    assert "window.PP_APORTE =" not in c.get("/").text
    monkeypatch.setenv("PACEPATH_APORTE_URL", "javascript:alert(1)")              # sólo https
    assert "window.PP_APORTE =" not in c.get("/").text
    monkeypatch.setenv("PACEPATH_APORTE_URL", "https://ko-fi.com/pacepath")
    assert 'window.PP_APORTE = "https://ko-fi.com/pacepath";' in c.get("/").text


def test_guia_beta_en_ingles():
    t = TestClient(app).get("/beta").text
    assert 'lang="en"' in t and "Send feedback" in t and "Add to Home Screen" in t and "long run 12k flat" in t


def test_terminos_de_uso():
    c = TestClient(app)
    for ruta in ["/terms", "/terminos"]:
        r = c.get(ruta)
        assert r.status_code == 200 and "Términos de uso" in r.text and "Terms of use" in r.text and "19.496" in r.text
    assert "/terminos" in c.get("/privacidad").text and "/terms" in c.get("/privacy").text
