"""Cuándo se corre: de noche, con calor, en hora punta."""
from datetime import datetime
from zoneinfo import ZoneInfo

from pacepath.momento import Momento, altura_sol, momento_de
from pacepath.sesion import MOMENTO, interpretar, requisitos

Z = ZoneInfo("America/Santiago")


def t(s):
    return datetime.fromisoformat(s).replace(tzinfo=Z)


def test_sol_en_santiago():
    assert altura_sol(-33.44, -70.65, t("2026-10-23 13:30")) > 60          # mediodía solar
    assert altura_sol(-33.44, -70.65, t("2026-10-23 21:30")) < -10
    assert -2 < altura_sol(-33.44, -70.65, t("2026-01-15 21:00")) < 0     # recién puesto, en verano


def test_momentos():
    assert momento_de(t("2026-10-23 07:30")) == Momento(punta=True)       # viernes temprano: ya amaneció
    assert momento_de(t("2026-10-23 20:45")).oscuro
    assert momento_de(t("2026-01-15 15:00")) == Momento(calor=True)
    assert momento_de(t("2026-10-25 08:00")).neutro                       # domingo: no hay hora punta
    assert momento_de(t("2026-06-20 06:30")).oscuro                       # invierno, antes del amanecer


def test_requisitos_segun_momento():
    s = interpretar("fondo 12k")
    normal = requisitos(s)
    for m, revisar in [
        (Momento(oscuro=True), lambda r: r.mult_sin_luz > 1 and r.peso_parque == 0),
        (Momento(calor=True), lambda r: r.mult_parque < normal.mult_parque and r.peso_parque > normal.peso_parque),
        (Momento(punta=True), lambda r: r.penal_semaforo_m > normal.penal_semaforo_m and r.mult_avenida > 1),
    ]:
        marca = MOMENTO.set(m)
        try:
            assert revisar(requisitos(s)), m
        finally:
            MOMENTO.reset(marca)
    assert requisitos(s) == normal                                       # fuera del pedido, nada cambia
