"""Una sesión → su plan, según el tipo."""
from __future__ import annotations

from raceengine_engine.context import GraphContext

from .continuo import planificar_circuito, planificar_tempo
from .sesion import Sesion, TipoSesion
from .idioma import tr
from .series import SinTramo, nodo_salida, planificar_series, radio_max


def planificar(ctx: GraphContext, sesion: Sesion, lat: float, lon: float,
               volver: bool = True, meta: tuple[float, float] | None = None,
               preferidas=None, radio_max_m: float | None = None) -> dict:
    """Como `_planificar`, sin salir de `radio_max_m` alrededor de la salida."""
    if not radio_max_m:
        return _planificar(ctx, sesion, lat, lon, volver, meta, preferidas)
    with radio_max(ctx, nodo_salida(ctx, lat, lon), radio_max_m):
        try:
            plan = _planificar(ctx, sesion, lat, lon, volver, meta, preferidas)
        except SinTramo as exc:
            km = radio_max_m / 1000
            raise SinTramo(f"{exc} " + tr(f"(Buscando a menos de {km:g} km de la salida: "
                                          "sube el radio o quítalo.)",
                                          f"(Searching within {km:g} km of the start: "
                                          "increase the radius or remove it.)")) from exc
    plan["radio_max_m"] = radio_max_m
    return plan


def _planificar(ctx: GraphContext, sesion: Sesion, lat: float, lon: float,
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
