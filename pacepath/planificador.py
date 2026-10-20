"""Una sesión → su plan, según el tipo."""
from __future__ import annotations

from raceengine_engine.context import GraphContext

from .continuo import planificar_circuito, planificar_tempo
from .sesion import Sesion, TipoSesion
from .series import planificar_series


def planificar(ctx: GraphContext, sesion: Sesion, lat: float, lon: float) -> dict:
    if sesion.tipo in (TipoSesion.SERIES, TipoSesion.CUESTAS):
        return planificar_series(ctx, sesion, lat, lon)
    if sesion.tipo is TipoSesion.TEMPO:
        return planificar_tempo(ctx, sesion, lat, lon)
    return planificar_circuito(ctx, sesion, lat, lon)
