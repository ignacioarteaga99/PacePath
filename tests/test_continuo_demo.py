"""Tempo, fondo, regenerativo y cuestas sobre la región sintética «demo»."""
import math
import numpy as np
import pytest

from raceengine_geodata import REGIONS

from pacepath.comunas import contexto
from pacepath.continuo import planificar_circuito, planificar_tempo
from pacepath.metricas import medir
from pacepath.parques import _nodos_en
from pacepath.sesion import interpretar, requisitos
from pacepath.series import SinTramo, planificar_series

D = REGIONS["demo"]


@pytest.fixture(scope="module")
def ctx():
    return contexto("demo")


def _encadenado(plan):
    # El trote llega al NODO más cercano al extremo exacto de la repetición; en
    # la región sintética las aristas miden ~60 m (en las reales, ~10 m).
    partes = [p for p in plan["partes"] if p.get("coordenadas")]
    for a, b in zip(partes, partes[1:]):
        x, y = a["coordenadas"][-1], b["coordenadas"][0]
        assert abs(x[0] - y[0]) < 8e-4 and abs(x[1] - y[1]) < 8e-4, (a["tipo"], b["tipo"])


@pytest.mark.parametrize("texto,objetivo", [("fondo 10k", 10_000), ("regenerativo 5k", 5_000)])
def test_circuito_mide_lo_pedido(ctx, texto, objetivo):
    p = planificar_circuito(ctx, interpretar(texto), D.lat, D.lon)
    assert p["recorrido"]["distancia_m"] == pytest.approx(objetivo, rel=0.10)
    c = p["partes"][0]["coordenadas"]
    # Vuelve a la salida.
    assert abs(c[0][0] - c[-1][0]) < 1e-4 and abs(c[0][1] - c[-1][1]) < 1e-4
    assert p["explicacion"][0].startswith(("Fondo", "Regenerativo"))


def test_tempo(ctx):
    p = planificar_tempo(ctx, interpretar("tempo 5k"), D.lat, D.lon)
    assert p["forma"] in ("corredor", "circuito")
    assert p["totales"]["tempo_m"] == pytest.approx(5000, rel=0.08)
    assert [x["tipo"] for x in p["partes"]][0] == "calentamiento"
    assert p["partes"][-1]["tipo"] == "vuelta_calma"
    _encadenado(p)


def test_cuestas_suben(ctx):
    try:
        p = planificar_series(ctx, interpretar("cuestas 6x200 4%"), D.lat, D.lon)
    except SinTramo:
        pytest.skip("la región sintética no tiene esa pendiente cerca")
    t = p["tramo"]
    assert t["pendiente_pct"] > 0                      # se recorre de subida
    assert abs(t["pendiente_pct"] - 4) <= requisitos(interpretar("cuestas 6x200 4%")).tolerancia_pendiente_pct + 0.1
    tipos = [x["tipo"] for x in p["partes"]]
    assert tipos.count("serie") == 6 and tipos.count("recuperacion") == 6
    sube = p["partes"][1]["coordenadas"]
    assert sube[-1][2] > sube[0][2]
    _encadenado(p)


def test_medir_ida_y_vuelta(ctx):
    g = ctx.graph
    e = [int(x) for x in range(g.indptr[100], g.indptr[100] + 1)]
    ida = e + [int(g.reverse[e[0]])]
    m = medir(ctx, ida, 0)
    assert m.repetido == pytest.approx(1.0)
    assert m.vueltas_en_u == 1


def test_poligono():
    cuadrado = np.array([[0, 0], [0, 1], [1, 1], [1, 0]], dtype=float)
    lat = np.array([0.5, 1.5, 0.2])
    lon = np.array([0.5, 0.5, 0.9])
    assert list(_nodos_en(lat, lon, cuadrado)) == [True, False, True]


def test_tope_de_desnivel_se_respeta_o_se_avisa(ctx):
    s = interpretar("fondo 8k").model_copy(update={"max_subida_m": 30})
    p = planificar_circuito(ctx, s, D.lat, D.lon)
    assert p["totales"]["max_subida_m"] == 30
    if p["totales"]["subida_m"] > 31:
        assert any("suba menos de 30 m" in a for a in p["avisos"])
    assert any("pediste como máximo 30 m" in l for l in p["explicacion"])


def test_corredores_no_fallan(ctx):
    from pacepath.continuo import pesos_sesion, por_corredor
    from pacepath.series import nodo_salida
    from pacepath.tramos import nodos_en_red
    req = requisitos(interpretar("fondo 10k"))
    casa = nodo_salida(ctx, D.lat, D.lon)
    assert nodos_en_red(ctx)[casa]                    # la salida está en la red principal
    for aristas, med, p in por_corredor(ctx, casa, 10_000, req, pesos_sesion(ctx, casa, 7500, req)):
        assert med.forma == "corredor"
        assert med.distancia_m == pytest.approx(10_000, rel=0.1)
        assert med.detalle["largo_m"] >= 1500


def _lejos(a, b, m):
    k = math.cos(math.radians(a[0]))
    return math.hypot(a[0] - b[0], (a[1] - b[1]) * k) * 111_195 > m


@pytest.mark.parametrize("texto", ["5x400", "tempo 3k"])
def test_sesion_sin_volver(ctx, texto):
    from pacepath.planificador import planificar
    s = interpretar(texto)
    p = planificar(ctx, s, D.lat, D.lon, volver=False)
    assert p["volver"] is False and p["con_meta"] is False
    vc = p["partes"][-1]
    assert vc["tipo"] == "vuelta_calma"
    if s.tipo.value == "series":
        # Trote suave de lo pedido (±10 %) desde el final de la última serie.
        assert abs(vc["distancia_m"] - s.vuelta_calma_m) <= 0.1 * s.vuelta_calma_m
    else:
        # Tempo: recorrido de solo ida; el bloque se ubica donde hay menos
        # detenciones y la vuelta a la calma es lo que queda.
        pedido = s.calentamiento_m + s.distancia_m + s.vuelta_calma_m
        assert abs(p["totales"]["distancia_m"] - pedido) <= 0.06 * pedido
    assert _lejos(p["llegada"], p["salida"], 300)
    assert p["llegada"] == vc["coordenadas"][-1][:2]
    assert any("sin volver a la salida" in x for x in p["explicacion"])


def test_series_hasta_la_meta(ctx):
    from pacepath.planificador import planificar
    g = ctx.graph
    meta = (float(g.lat[0]), float(g.lon[0]))
    p = planificar(ctx, interpretar("5x400"), D.lat, D.lon, volver=False, meta=meta)
    assert p["con_meta"] is True
    assert not _lejos(p["llegada"], meta, 60)
    assert any("hasta la meta" in x for x in p["explicacion"])
