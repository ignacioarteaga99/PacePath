"""Una sesión → su plan, según el tipo."""
from __future__ import annotations

from raceengine_engine.context import GraphContext

from .continuo import planificar_circuito, planificar_tempo
from .momento import Momento
from .sesion import MOMENTO, Sesion, TipoSesion
from .idioma import tr
from .series import SinTramo, nodo_salida, planificar_series, radio_max


def planificar(ctx: GraphContext, sesion: Sesion, lat: float, lon: float,
               volver: bool = True, meta: tuple[float, float] | None = None,
               preferidas=None, radio_max_m: float | None = None,
               forma: str = "auto", n_vueltas: int | None = None,
               momento: Momento | None = None, opcion: int = 0) -> dict:
    """Como `_planificar`, sin salir de `radio_max_m` alrededor de la salida y
    según cuándo se corre (`momento`: de noche, con calor, en hora punta)."""
    marca = MOMENTO.set(momento)
    try:
        plan = _con_radio(ctx, sesion, lat, lon, volver, meta, preferidas, radio_max_m, forma, n_vueltas, opcion)
    finally:
        MOMENTO.reset(marca)
    if momento is not None and not momento.neutro:
        plan["momento"] = {"oscuro": momento.oscuro, "calor": momento.calor, "punta": momento.punta}
        plan.setdefault("avisos", []).insert(0, _aviso_momento(momento))
    return plan


def _aviso_momento(m: Momento) -> str:
    partes = []
    if m.oscuro:
        partes.append(tr("es de noche: la ruta evita senderos de parque y caminos sin luz",
                         "it's dark: the route avoids unlit park trails and dirt paths"))
    elif m.calor:
        partes.append(tr("hace calor: la ruta busca más parques (lo más parecido a sombra que hay en el mapa)",
                         "it's hot: the route looks for more parks (the closest thing to shade on the map)"))
    if m.punta:
        partes.append(tr("hora punta: evita más semáforos, cruces y avenidas",
                         "rush hour: it avoids traffic lights, crossings and avenues even more"))
    texto = "; ".join(partes)
    return texto[0].upper() + texto[1:] + "."


def _con_radio(ctx, sesion, lat, lon, volver, meta, preferidas, radio_max_m, forma, n_vueltas, opcion) -> dict:
    if not radio_max_m:
        return _planificar(ctx, sesion, lat, lon, volver, meta, preferidas, forma, n_vueltas, opcion)
    with radio_max(ctx, nodo_salida(ctx, lat, lon), radio_max_m):
        try:
            plan = _planificar(ctx, sesion, lat, lon, volver, meta, preferidas, forma, n_vueltas, opcion)
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
                preferidas=None, forma: str = "auto", n_vueltas: int | None = None,
                opcion: int = 0) -> dict:
    """`preferidas`: float32[E] con calles de siempre y tramos favoritos (o None).
    Por ahora pesan en tempo, fondo y regenerativo.

    Sin `volver`, la ruta termina en `meta` o, sin meta, donde se cumple lo pedido
    (en series, cuestas y tempo, al final de la vuelta a la calma)."""
    if sesion.tipo in (TipoSesion.SERIES, TipoSesion.CUESTAS):
        # Series y cuestas: la sesión son las repeticiones; no hay vuelta que armar.
        return planificar_series(ctx, sesion, lat, lon, opcion=opcion)
    if sesion.tipo is TipoSesion.TEMPO:
        return planificar_tempo(ctx, sesion, lat, lon, preferidas=preferidas,
                                volver=volver, meta=meta, forma=forma, n_vueltas=n_vueltas, opcion=opcion)
    return planificar_circuito(ctx, sesion, lat, lon, volver=volver, meta=meta,
                               preferidas=preferidas, forma=forma, n_vueltas=n_vueltas, opcion=opcion)
