"""Beta cerrada: usuario y clave antes de entrar, activable desde el panel."""
import pytest
from fastapi.testclient import TestClient

from pacepath import acceso, api, registro
from pacepath.api import app

ADMIN = {"X-Admin-Clave": "clave-larga-de-prueba"}


@pytest.fixture
def beta(monkeypatch, tmp_path):
    """Base nueva (como un servidor recién desplegado) y la beta como venga por defecto."""
    monkeypatch.setenv("PACEPATH_ADMIN", "clave-larga-de-prueba")
    monkeypatch.setattr(api.time, "sleep", lambda s: None)
    anterior = registro._escritor
    registro.configurar(str(tmp_path / "base.sqlite"))
    acceso.olvidar()
    yield
    registro._escritor = anterior
    acceso.olvidar()


def entrar(cli, usuario="tester", clave="admin123"):
    return cli.post("/api/beta/entrar", json={"usuario": usuario, "clave": clave})


def test_por_defecto_pide_tester_y_admin123(beta):
    cli = TestClient(app)
    r = cli.get("/")
    assert r.status_code == 200 and "Beta privada" in r.text and "window.PP_" not in r.text
    assert r.headers["cache-control"] == "no-store"
    r = cli.post("/api/sesion", json={"texto": "6x1k"})
    assert r.status_code == 401 and r.headers["x-pacepath-beta"] == "1"
    assert entrar(cli, clave="otra").status_code == 401
    assert entrar(cli, usuario="otro").status_code == 401
    assert entrar(cli, usuario=" Tester ").status_code == 200          # el usuario no distingue mayúsculas
    assert cli.cookies.get(acceso.COOKIE)
    assert "Beta privada" not in cli.get("/").text
    assert cli.post("/api/sesion", json={"texto": "6x1k"}).status_code == 200


def test_quedan_abiertos_el_panel_lo_legal_y_los_archivos(beta):
    cli = TestClient(app)
    for ruta in ("/admin", "/api/salud", "/privacidad", "/terms", "/beta", "/manifest.webmanifest", "/sw.js", "/icons/icono-192.png"):
        r = cli.get(ruta)
        assert r.status_code == 200 and "Beta privada" not in r.text, ruta


def test_el_panel_la_desactiva_y_cambia_la_clave(beta):
    cli, tester = TestClient(app), TestClient(app)
    assert cli.get("/api/admin/acceso").status_code == 404                     # sin la clave de admin
    assert cli.get("/api/admin/acceso", headers=ADMIN).json() == {"activo": True, "usuario": "tester", "clave_inicial": True}
    entrar(tester)
    assert tester.post("/api/sesion", json={"texto": "6x1k"}).status_code == 200

    # Nueva clave: la sesión del tester deja de valer; la clave vieja ya no entra.
    r = cli.post("/api/admin/acceso", headers=ADMIN, json={"clave": "carrera-2026"})
    assert r.json() == {"activo": True, "usuario": "tester", "clave_inicial": False}
    assert tester.post("/api/sesion", json={"texto": "6x1k"}).status_code == 401
    assert entrar(tester).status_code == 401
    assert entrar(tester, clave="carrera-2026").status_code == 200

    # Se guarda en la base: sobrevive a un reinicio (Render duerme).
    acceso.olvidar()
    assert acceso.estado()["clave_inicial"] is False and acceso.entrar("tester", "carrera-2026")
    assert "carrera-2026" not in str(registro.ajustes())                       # la clave nunca tal cual

    # Desactivada: abierta a todos.
    assert cli.post("/api/admin/acceso", headers=ADMIN, json={"activo": False}).json()["activo"] is False
    assert TestClient(app).post("/api/sesion", json={"texto": "6x1k"}).status_code == 200
    assert "Beta privada" not in TestClient(app).get("/").text
    assert cli.post("/api/admin/acceso", headers=ADMIN, json={"clave": "abc"}).status_code == 422   # muy corta


def test_sin_base_de_datos_queda_la_beta_inicial(beta, monkeypatch):
    def caida():
        raise OSError("Neon no responde")
    monkeypatch.setattr(registro, "ajustes", caida)
    acceso.olvidar()
    assert acceso.activo() and acceso.entrar("tester", "admin123")
