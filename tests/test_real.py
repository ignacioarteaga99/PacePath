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


def test_comuna_con_mas_margen_hasta_el_borde():
    # Parque Bicentenario (Vitacura) queda más adentro del grafo de Las Condes
    # (radio 9 km) que del de Vitacura (7 km): más calle para todos lados.
    assert comuna_para(-33.3990, -70.6010) == "las_condes"
    assert comuna_para(-33.4370, -70.6345) == "santiago_centro"     # Plaza Italia


def test_vueltas_dentro_de_un_parque():
    # Desde el Parque Araucano, un regenerativo da vueltas por dentro del parque
    # (antes salía a la calle: el parque era una isla en la red corrible).
    from pacepath.planificador import planificar
    p = planificar(contexto("las_condes"), interpretar("regenerativo 8k"), -33.4030, -70.5730)
    r = p["recorrido"]
    assert r["forma"] == "parque" and r["parque_pct"] >= 80 and r["semaforos"] <= 2
    assert abs(r["distancia_m"] - 8000) <= 0.03 * 8000
    assert "vueltas" in p["explicacion"][-1]


def test_de_noche_se_avisa():
    from pacepath.momento import Momento
    from pacepath.planificador import planificar
    p = planificar(contexto("providencia"), interpretar("fondo 10k"), *SALIDA, momento=Momento(oscuro=True))
    assert p["avisos"][0].startswith("Es de noche") and p["momento"]["oscuro"]
