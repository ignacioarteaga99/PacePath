"""Sobre el grafo real de Providencia (Release «graphs» de RaceEngine)."""
import pytest

from pacepath.comunas import comuna_para, contexto
from pacepath.sesion import interpretar
from pacepath.series import planificar_series

pytestmark = pytest.mark.real
SALIDA = (-33.4258, -70.61)    # centro de Providencia


def test_comuna_por_coordenada():
    assert comuna_para(*SALIDA) == "providencia"
    assert comuna_para(-41.47, -72.94) is None        # Puerto Montt: sin mapa


@pytest.mark.parametrize("texto", ["6x1k", "8x400 r60", "10x200"])
def test_series_sin_semaforos_en_providencia(texto):
    ctx = contexto("providencia")
    p = planificar_series(ctx, interpretar(texto), *SALIDA)
    t = p["tramo"]
    assert t["semaforos"] == 0
    assert t["cruces_mayores"] == 0
    assert t["largo_m"] == interpretar(texto).distancia_rep_m
    assert p["avisos"][:-1] == [] or "calentamiento" in p["avisos"][0]
    assert t["acercamiento_m"] < 5000


@pytest.mark.parametrize("texto", ["tempo 8k", "fondo 15k", "regenerativo 6k",
                                   "cuestas 8x300 6%"])
def test_otros_tipos_en_providencia(texto):
    from pacepath.planificador import planificar
    p = planificar(contexto("providencia"), interpretar(texto), *SALIDA)
    assert p["explicacion"]
    assert p["totales"]["distancia_m"] > 3000
