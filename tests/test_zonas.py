"""«Preparando tu zona»: pedir, esperar, avisar (sin red: GitHub y el manifiesto simulados)."""
import pytest
from fastapi.testclient import TestClient

from pacepath import api, registro, zonas
from raceengine_geodata import grid

PARIS = (48.8566, 2.3522)


@pytest.fixture
def gh(monkeypatch):
    """GitHub y el manifiesto de mentira; devuelve (llamadas, manifiesto)."""
    llamadas, man = [], {}
    monkeypatch.setenv("PACEPATH_GITHUB_TOKEN", "x")
    monkeypatch.setattr(zonas, "_github", lambda repo, wf, ref, inputs: llamadas.append((repo, wf, inputs)) or True)
    monkeypatch.setattr(zonas, "_manifiesto", lambda fresco=False: man)
    monkeypatch.setattr(zonas, "_nombre", lambda lat, lon: "París")
    registro.sql("DELETE FROM pp_zonas")
    return llamadas, man


def test_sin_token_no_se_pide_nada(monkeypatch):
    monkeypatch.delenv("PACEPATH_GITHUB_TOKEN", raising=False)
    assert zonas.pedir(*PARIS)["estado"] == "no_disponible"


def test_pedir_esperar_y_avisar(gh, monkeypatch):
    llamadas, man = gh
    avisos = []
    monkeypatch.setattr(zonas.correo, "activo", lambda: True)
    monkeypatch.setattr(zonas.correo, "en_fondo", lambda fn, para, *a, lang=None: avisos.append((para, a[1])))
    monkeypatch.setattr(zonas.cuentas, "por_id", lambda uid: {"correo": f"{uid}@x.cl", "nombre": uid, "activo": 1})
    zid = grid.celda(*PARIS)
    for cuenta in ("ana", "beto", "ana"):
        marca = zonas.uso.consulta.set({"cuenta": cuenta})
        z = zonas.pedir(*PARIS)
        zonas.uso.consulta.reset(marca)
        assert z["estado"] == "pedida" and z["id"] == zid and z["nombre"] == "París"
    assert [c[1] for c in llamadas] == ["build-region.yml"]            # una sola construcción
    assert llamadas[0][2]["region"] == zid
    assert zonas.estado(zid)["estado"] == "pedida"
    man[zid] = {"id": zid, "label": "Paris, France"}                     # el workflow la publicó
    e = zonas.estado(zid)
    assert e == {"id": zid, "estado": "lista", "nombre": "Paris, France"}
    assert llamadas[-1][1] == "nombres.yml" and llamadas[-1][2] == {"comunas": zid}
    assert sorted(avisos) == [("ana@x.cl", "Paris, France"), ("beto@x.cl", "Paris, France")]
    assert zonas.revisar() == 0


def test_tope_diario_y_reenvio(gh, monkeypatch):
    llamadas, _ = gh
    monkeypatch.setenv("PACEPATH_ZONAS_DIA", "1")
    assert zonas.pedir(*PARIS)["estado"] == "pedida"
    assert zonas.pedir(40.4168, -3.7038)["estado"] == "limite"           # Madrid: ya no hay cupo hoy
    # 20 min sin aparecer: se vuelve a mandar (la espera en GitHub pudo cancelarse)
    registro.sql("UPDATE pp_zonas SET enviada = '2020-01-01T00:00:00Z'")
    zonas.revisar()
    assert len(llamadas) == 2
    registro.sql("UPDATE pp_zonas SET pedida = '2020-01-01T00:00:00Z'")   # una hora sin aparecer: falló
    zonas.revisar()
    assert zonas.estado(grid.celda(*PARIS))["estado"] == "fallida"


def test_api_ruta_fuera_del_mapa_prepara_la_zona(gh):
    cli = TestClient(api.app)
    r = cli.post("/api/ruta", json={"lat": PARIS[0], "lon": PARIS[1], "texto": "fondo 10k"})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["preparando"]["estado"] == "pedida" and "París" in d["mensaje"]
    assert cli.get(f"/api/zona/{d['preparando']['id']}").json()["estado"] == "pedida"
    assert cli.get("/api/zona/providencia").json()["estado"] == "desconocida"
