"""Una sesión → su plan, según el tipo."""
from __future__ import annotations

from raceengine_engine.context import GraphContext

from .continuo import planificar_circuito, planificar_tempo
from .sesion import Sesion, TipoSesion
from .series import planificar_series


def planificar(ctx: GraphContext, sesion: Sesion, lat: float, lon: float,
               volver: bool = True, meta: tuple[float, float] | None = None,
               preferidas=None) -> dict:
    """`preferidas`: float32[E] con calles de siempre y tramos favoritos (o None).
    Por ahora pesan en tempo, fondo y regenerativo.

    Sin `volver`, la ruta termina en `meta` o, sin meta, donde se cumple lo pedido
    (en series, cuestas y tempo, al final de la vuelta a la calma)."""
    if sesion.tipo in (TipoSesion.SERIES, TipoSesion.CUESTAS):
        return planificar_series(ctx, sesion, lat, lon, volver=volver, meta=meta)
    if sesion.tipo is TipoSesion.TEMPO:
        return planificar_tempo(ctx, sesion, lat, lon, preferidas=preferidas,
                                volver=volver, meta=meta)
    return planificar_circuito(ctx, sesion, lat, lon, volver=volver, meta=meta,
                               preferidas=preferidas)
