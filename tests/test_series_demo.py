"""Series sobre la región sintética «demo» de RaceEngine: sin red."""
import numpy as np
import pytest

from raceengine_geodata import REGIONS

from pacepath.comunas import contexto
from pacepath.gpx import plan_a_gpx
from pacepath.sesion import interpretar, requisitos
from pacepath.series import SinTramo, buscar_tramos, planificar_series
from pacepath.tramos import trazos


@pytest.fixture(scope="module")
def ctx():
    return contexto("demo")


@pytest.fixture(scope="module")
def plan(ctx):
    d = REGIONS["demo"]
    return planificar_series(ctx, interpretar("5x400"), d.lat, d.lon)


def test_trazos_son_continuos(ctx):
    g = ctx.graph
    for t in trazos(ctx)[:200]:
        # Cada arista sale del nodo donde terminó la anterior.
        assert np.array_equal(ctx.esrc[t.aristas], t.nodos[:-1])
        assert np.array_equal(g.indices[t.aristas], t.nodos[1:])
        assert t.cum[-1] == pytest.approx(g.elen[t.aristas].sum(), rel=1e-5)
        assert t.cruce[0] == 0 and t.cruce[-1] == 0


def test_tramo_cumple_requisitos(ctx, plan):
    t = plan["tramo"]
    req = requisitos(interpretar("5x400"))
    assert t["largo_m"] == 400
    assert t["semaforos"] == 0 and t["cruces_mayores"] == 0
    assert t["desnivel_m"] <= req.max_desnivel_tramo_m
    assert t["rectitud"] >= req.min_rectitud
    # La longitud de la geometría coincide con la de la repetición.
    c = np.asarray(t["coordenadas"])
    k = np.cos(np.radians(c[:, 0].mean()))
    d = np.hypot(np.diff(c[:, 0]), np.diff(c[:, 1]) * k).sum() * 111_195
    assert d == pytest.approx(400, rel=0.02)


def test_plan_encadenado(plan):
    partes = plan["partes"]
    assert [p["tipo"] for p in partes] == ["calentamiento"] + ["serie"] * 5
    cerca = lambda a, b: abs(a[0] - b[0]) < 2e-4 and abs(a[1] - b[1]) < 2e-4  # ~20 m
    for a, b in zip(partes, partes[1:]):
        if a["coordenadas"] and b["coordenadas"]:
            assert cerca(a["coordenadas"][-1], b["coordenadas"][0]), (a["tipo"], b["tipo"])
    tot = plan["totales"]
    assert tot["series_m"] == 2000
    assert tot["distancia_m"] == pytest.approx(
        tot["calentamiento_m"] + tot["series_m"], abs=2)
    assert plan["explicacion"][0].startswith("Las series van por")


def test_gpx(plan):
    x = plan_a_gpx(plan)
    assert x.count("<trkseg>") == 6          # llegar + 5 series (sin vuelta a la calma)
    assert "Serie 5 · fin" in x


def test_sin_tramo_imposible(ctx):
    g = ctx.graph
    casa = int(g.nearest_node(REGIONS["demo"].lat, REGIONS["demo"].lon))
    req = requisitos(interpretar("2x5000"))
    with pytest.raises(SinTramo):
        buscar_tramos(ctx, casa, req, holgura_m=500)


def test_explicacion_series_sin_traslado_en_el_total(plan):
    # El total es el bloque; ida y vuelta se cuentan aparte, como traslado.
    ex = plan["explicacion"]
    assert "Series: 5 × 400 m = 2,00 km." in ex
    assert any(l.startswith("Para llegar:") and "calentamiento" in l for l in ex)
    assert not any(l.startswith(("Para volver", "Vuelta a la calma", "Después")) for l in ex)
    assert not any(l.startswith("Total") for l in ex)
