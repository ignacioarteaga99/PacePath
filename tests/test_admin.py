"""Panel de administración: registro de consultas y su acceso."""
import io
import json
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
                    1, 200, 850, None, "es", "iPhone", None, 10, 2000, 0, '{"calculo":800}')])
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


def test_con_postgres_se_guarda_de_a_lotes_y_el_panel_fuerza_el_guardado(tmp_path, monkeypatch):
    """Con un intervalo largo nada se escribe hasta que alguien pide los datos."""
    monkeypatch.setenv("PACEPATH_REGISTRO_SEG", "3600")
    esc = registro._Escritor(registro._Base(str(tmp_path / "r.sqlite")))
    esc.anotar((*registro.ahora(), "0123456789", 0, "visita", "visita", None, None, None, 1, 200, 5, None, "es", "iPhone",
                None, 0, 1234, None, None))
    import time
    time.sleep(0.3)
    assert esc.cola.unfinished_tasks == 1                    # sigue en memoria
    assert esc.vaciar(3)                                     # el panel (o el apagado) lo guarda al tiro
    assert len(esc.base.consultar(registro.hoy(), registro.hoy(), 10)) == 1


def test_rendimiento_del_motor(admin):
    """Cada ruta deja sus etapas (espera, mapa, cálculo), CPU, memoria y calidad;
    el panel ve además el estado del proceso."""
    from raceengine_geodata import REGIONS
    d = REGIONS["demo"]
    cli = TestClient(app)
    for texto in ("fondo 5k", "5x400"):
        assert cli.post("/api/ruta", json={"lat": d.lat, "lon": d.lon, "texto": texto, "comuna": "demo"}).status_code == 200
    filas = admin(usuario=cli.cookies.get("pp_u"), consulta="ruta")
    assert len(filas) == 2
    m = {f["detalle"].split(" · ")[0] if f["detalle"] else "": json.loads(f["motor"]) for f in filas}
    fondo = next(v for k, v in m.items() if "fondo" in k)
    assert {"espera", "mapa", "frio", "calculo", "cpu", "mem", "cola", "km"} <= set(fondo)
    assert fondo["calculo"] >= 0 and fondo["mem"] > 0 and abs(fondo["error_pct"]) < 15 and "sem_km" in fondo
    assert all(f["ms"] >= v["calculo"] for f, v in zip(filas, [json.loads(f["motor"]) for f in filas]))
    assert cli.get("/api/admin/motor").status_code == 404
    e = cli.get("/api/admin/motor", headers={"X-Admin-Clave": "clave-larga-de-prueba"}).json()
    assert "demo" in e["grafos"] and e["memoria_mb"] > 0 and e["maximo_mb"] >= e["memoria_mb"] - 1


def test_en_vivo(admin, monkeypatch):
    """Mientras una ruta se calcula, el panel la ve en curso con su etapa; al
    terminar pasa a recientes. Las muestras del proceso llegan cada segundo."""
    import threading
    import time as _t
    from raceengine_geodata import REGIONS
    from pacepath import rendimiento
    d = REGIONS["demo"]
    adentro, soltar = threading.Event(), threading.Event()
    original = api.planificar

    def lento(*a, **k):
        adentro.set()
        soltar.wait(5)
        return original(*a, **k)
    monkeypatch.setattr(api, "planificar", lento)
    cli, hdr = TestClient(app), {"X-Admin-Clave": "clave-larga-de-prueba"}
    assert cli.get("/api/admin/vivo").status_code == 404
    h = threading.Thread(target=lambda: cli.post("/api/ruta", json={"lat": d.lat, "lon": d.lon,
                                                                     "texto": "fondo 5k", "comuna": "demo"}))
    h.start()
    assert adentro.wait(5)
    v = cli.get("/api/admin/vivo", headers=hdr).json()
    p = next(x for x in v["en_curso"] if x["consulta"] == "ruta")
    assert p["etapa"] == "calculando" and p["que"] == "fondo 5k" and p["comuna"] == "demo" and p["lleva_ms"] >= 0
    soltar.set()
    h.join(10)
    _t.sleep(1.2)
    v = cli.get("/api/admin/vivo", headers=hdr).json()
    assert not [x for x in v["en_curso"] if x["consulta"] == "ruta"]
    r = next(x for x in v["recientes"] if x["consulta"] == "ruta")
    assert r["exito"] and r["motor"]["calculo"] >= 0
    assert v["serie"] and len(v["serie"][-1]) == 6 and v["serie"][-1][1] > 0
    assert cli.get(f"/api/admin/vivo?desde={v['t']}", headers=hdr).json()["serie"] == []
    assert rendimiento._mediana("calculo:fondo") is not None          # para estimar el avance la próxima vez
