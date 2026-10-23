"""Cancelar una ruta mientras se calcula, y la caché de rutas recientes."""
import threading
import time

from fastapi.testclient import TestClient
from raceengine_geodata import REGIONS

from pacepath import api, cancelar, recientes
from pacepath.api import app

D = REGIONS["demo"]
CUERPO = {"lat": D.lat, "lon": D.lon, "texto": "fondo 5k", "comuna": "demo"}


def _lento(monkeypatch, llamadas):
    """Un planificador que no termina hasta que lo cancelan (revisa como el motor)."""
    def planificar(*a, **k):
        llamadas.append(1)
        for _ in range(500):
            cancelar.revisar()
            time.sleep(0.01)
        raise AssertionError("no lo cancelaron")
    monkeypatch.setattr(api, "planificar", planificar)


def test_cancelar_corta_el_calculo_y_suelta_el_turno(monkeypatch):
    llamadas = []
    _lento(monkeypatch, llamadas)
    cli = TestClient(app)
    res = {}
    h = threading.Thread(target=lambda: res.update(r=cli.post("/api/ruta", json=CUERPO, headers={"X-Pedido": "abc123"})))
    t0 = time.time()
    h.start()
    while not llamadas:
        time.sleep(0.01)
    assert cli.post("/api/cancelar?pedido=abc123").json() == {"ok": True}
    h.join(5)
    assert res["r"].status_code == 499 and time.time() - t0 < 3
    assert not api._motor.locked()                                   # el turno quedó libre
    assert cli.post("/api/cancelar?pedido=abc123").json() == {"ok": False}   # ya terminó


def test_cancelar_mientras_espera_turno_ni_empieza(monkeypatch):
    llamadas = []
    _lento(monkeypatch, llamadas)
    cli = TestClient(app)
    res = {}
    api._motor.acquire()                                              # otro pedido ocupando el motor
    try:
        h = threading.Thread(target=lambda: res.update(r=cli.post("/api/ruta", json=CUERPO, headers={"X-Pedido": "fila1"})))
        h.start()
        time.sleep(0.4)
        assert cli.post("/api/cancelar?pedido=fila1").json() == {"ok": True}
    finally:
        api._motor.release()
    h.join(5)
    assert res["r"].status_code == 499 and llamadas == []


def test_caché_de_rutas_recientes(monkeypatch):
    cli = TestClient(app)
    primera = cli.post("/api/ruta", json=CUERPO).json()
    original, llamadas = api.planificar, []
    monkeypatch.setattr(api, "planificar", lambda *a, **k: llamadas.append(1) or original(*a, **k))
    # Lo mismo (la salida se mueve 3 m: el GPS baila): de la caché, sin calcular.
    otra = cli.post("/api/ruta", json=CUERPO | {"lat": D.lat + 0.00002}).json()
    assert llamadas == [] and otra == primera
    assert recientes.estado()["aciertos"] == 1
    # Cambia lo que importa: se calcula.
    cli.post("/api/ruta", json=CUERPO | {"opcion": 1})
    cli.post("/api/ruta", json=CUERPO, headers={"X-Idioma": "en"})
    cli.post("/api/ruta", json=CUERPO | {"texto": "fondo 6k"})
    assert len(llamadas) == 3
    # El GPX después de ver la ruta también sale de la caché.
    assert cli.post("/api/ruta.gpx", json=CUERPO).status_code == 200 and len(llamadas) == 3
