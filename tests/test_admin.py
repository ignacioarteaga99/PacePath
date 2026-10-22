"""Panel de administración: registro de consultas y su acceso."""
import io
import os
import urllib.error

import pytest
from fastapi.testclient import TestClient

from pacepath import api, registro, strava
from pacepath.api import app


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setenv("PACEPATH_ADMIN", "clave-larga-de-prueba")
    monkeypatch.setattr(api.time, "sleep", lambda s: None)

    def filas(**filtro):
        registro.vaciar()
        r = TestClient(app).get("/api/admin/eventos", headers={"X-Admin-Clave": "clave-larga-de-prueba"})
        assert r.status_code == 200
        j = r.json()
        todas = [dict(zip(j["columnas"], f)) for f in j["filas"]]
        return [f for f in todas if all(f.get(k) == v for k, v in filtro.items())]
    return filas


def test_panel_sin_clave_no_existe(monkeypatch):
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    cli = TestClient(app)
    monkeypatch.delenv("PACEPATH_ADMIN", raising=False)
    assert cli.get("/api/admin/eventos", headers={"X-Admin-Clave": ""}).status_code == 404
    monkeypatch.setenv("PACEPATH_ADMIN", "clave-larga-de-prueba")
    assert cli.get("/api/admin/eventos").status_code == 404
    assert cli.get("/api/admin/eventos", headers={"X-Admin-Clave": "otra"}).status_code == 404
    assert cli.get("/api/admin/eventos", headers={"X-Admin-Clave": "ñ".encode("latin-1")}).status_code == 404   # no ASCII: no revienta
    r = cli.get("/admin")
    assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex"


def test_cada_consulta_queda_registrada_con_usuario_anonimo(admin):
    cli = TestClient(app)
    r = cli.post("/api/sesion", json={"texto": "6x1k"})
    usuario = r.cookies.get("pp_u") or cli.cookies.get("pp_u")
    assert usuario and len(usuario) == 10
    cli.post("/api/sesion", json={"texto": "tempo 8k"})            # mismo teléfono, mismo usuario
    filas = admin(usuario=usuario, consulta="interpretar")
    assert {f["detalle"] for f in filas} == {"6x1k", "tempo 8k"}
    assert {f["tipo"] for f in filas} == {"series", "tempo"}
    assert all(f["exito"] == 1 and f["estado"] == 200 and f["ms"] >= 0 and f["strava"] == 0 for f in filas)


def test_errores_quedan_con_su_mensaje(admin):
    cli = TestClient(app)
    cli.post("/api/ruta", json={"lat": -33.02, "lon": -71.55, "texto": "6x1k"})      # Viña del Mar
    f = admin(usuario=cli.cookies.get("pp_u"), consulta="ruta")[0]
    assert f["exito"] == 0 and f["estado"] == 422 and f["evento"] == "fuera_de_mapa"
    assert "Todavía no hay mapa" in f["error"] and f["comuna"] == "Viña del Mar"

    cli.post("/api/ruta", json={"lat": 999, "lon": 0, "texto": "6x1k"})
    f = admin(usuario=cli.cookies.get("pp_u"), consulta="ruta", estado=422, evento="ruta")[0]
    assert f["error"].startswith("datos inválidos") and "lat" in f["error"]

    cli.post("/api/evento", json={"tipo": "sync_error", "detalle": "página 3: Load failed"})
    f = admin(usuario=cli.cookies.get("pp_u"), evento="sync_error")[0]
    assert f["exito"] == 0 and f["error"] == "página 3: Load failed"


def test_no_se_registra_lo_que_no_aporta(admin):
    cli = TestClient(app)
    cli.get("/api/salud")
    cli.get("/api/lugares", params={"q": "Pocuro 2500"})
    u = cli.cookies.get("pp_u")
    filas = admin(usuario=u)
    assert [f["consulta"] for f in filas] == ["busqueda"]
    assert "Pocuro" not in str(filas)                                    # nunca la dirección buscada


def test_strava_limite_de_uso_es_429(admin, monkeypatch):
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secreto-de-prueba")
    s = strava.Sesion(access_token="a", refresh_token="r", expires_at=4_000_000_000, atleta_id=1,
                      nombre="Ana", alcance="read,activity:read_all")
    def espera(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"{}"))
    monkeypatch.setattr(strava.urllib.request, "urlopen", espera)
    cli = TestClient(app)
    cli.cookies.set(strava.COOKIE, s.cifrar())
    r = cli.get("/api/strava/actividades", params={"pagina": 2})
    assert r.status_code == 429 and "esperar" in r.json()["detail"]
    f = admin(usuario=cli.cookies.get("pp_u"), consulta="strava_sync")[0]
    assert f["strava"] == 1 and f["exito"] == 0 and "esperar" in f["error"]


@pytest.mark.skipif(not os.environ.get("PACEPATH_TEST_PG"), reason="sin Postgres de prueba (PACEPATH_TEST_PG)")
def test_postgres_ida_y_vuelta():
    base = registro._Base(os.environ["PACEPATH_TEST_PG"])
    base._ejecutar("DROP TABLE IF EXISTS pp_eventos")
    base._lista = False
    ts, dia = registro.ahora()
    base.insertar([(ts, dia, "abcdef0123", 1, "ruta", "ruta", "series", "providencia", "6x1k",
                    1, 200, 850, None, "es", "iPhone")])
    base.podar()
    filas = base.consultar(dia, dia, 10)
    assert filas and filas[0][2] == "abcdef0123" and filas[0][11] == 850


def test_primer_uso_sin_configurar_no_se_traba(tmp_path, monkeypatch):
    """En el servidor nadie llama a configurar(): la primera fila lo hace sola."""
    import threading
    monkeypatch.setenv("DATABASE_URL", str(tmp_path / "r.sqlite"))
    monkeypatch.setattr(registro, "_escritor", None)
    hilo = threading.Thread(target=registro.anotar, args=({"consulta": "visita", "usuario": "0123456789"},), daemon=True)
    hilo.start()
    hilo.join(3)
    assert not hilo.is_alive()
    assert registro.vaciar() and registro.consultar(registro.hoy(), registro.hoy())["filas"]
