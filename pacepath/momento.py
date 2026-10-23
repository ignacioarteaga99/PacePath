"""Cuándo se corre: de noche, con calor o en hora punta (en Santiago).

Cambia lo que conviene en la calle:
  · De noche (el sol más de 4° bajo el horizonte): nada de senderos de parque
    ni caminos de tierra sin luz. Las veredas no cuentan como oscuras aunque
    OpenStreetMap no les marque `lit`: casi nunca se marca, y las alumbra el
    poste de la calle (en las comunas, el 90 % de las calles tiene `lit`; las
    veredas mapeadas aparte, el 10 %).
  · Con calor (verano, de 11 a 19 h): más parque. No hay datos de sombra; los
    parques son la mejor aproximación.
  · En hora punta (días hábiles 7:00–9:30 y 17:30–20:30): semáforos y cruces
    más caros (se espera más) y avenidas más caras (tráfico y humo).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ZONA = ZoneInfo("America/Santiago")
SANTIAGO = (-33.44, -70.65)
SOL_NOCHE = -4.0               # grados: entre el ocaso y el fin del crepúsculo civil
MESES_CALOR = (12, 1, 2, 3)


@dataclass(frozen=True)
class Momento:
    oscuro: bool = False
    calor: bool = False
    punta: bool = False

    @property
    def neutro(self) -> bool:
        return not (self.oscuro or self.calor or self.punta)


def altura_sol(lat: float, lon: float, t: datetime) -> float:
    """Altura del sol sobre el horizonte, en grados (fórmulas de la NOAA)."""
    t = t.astimezone(timezone.utc)
    h = t.hour + t.minute / 60 + t.second / 3600
    g = 2 * math.pi / 365 * (t.timetuple().tm_yday - 1 + (h - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    ecuacion = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                         - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    angulo = math.radians((h * 60 + ecuacion + 4 * lon) / 4 - 180)
    la = math.radians(lat)
    cos_cenit = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(angulo)
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_cenit))))


def momento_de(cuando: datetime | None = None, lat: float = SANTIAGO[0], lon: float = SANTIAGO[1]) -> Momento:
    t = cuando or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    local = t.astimezone(ZONA)
    hora = local.hour + local.minute / 60
    habil = local.weekday() < 5
    return Momento(
        oscuro=altura_sol(lat, lon, t) < SOL_NOCHE,
        calor=local.month in MESES_CALOR and 11 <= hora < 19,
        punta=habil and (7 <= hora < 9.5 or 17.5 <= hora < 20.5),
    )
