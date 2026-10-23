"""Trazar una ruta a mano: el corredor marca puntos y la ruta va por la calle.

Cada punto se une con el anterior por el camino corrible MÁS CORTO (calles y
sendas de parque, sin vías privadas ni túneles). Se usa la distancia pura y no
los pesos de una sesión: al trazar a mano, el corredor decide por dónde va
poniendo puntos, y la ruta debe hacer lo predecible.

Sin estado: el cliente manda todos los puntos en cada cambio. Con pocas decenas
de puntos son pocas decenas de A* cortos.
"""
from __future__ import annotations

import numpy as np

from raceengine_engine.context import GraphContext
from raceengine_routing import RoutingWeights

from .idioma import tr
from .continuo import _coords, _marcas, _resumen_medida
from .metricas import medir
from .series import _calles_trote, aviso_srtm
from .tramos import aristas_corribles, nodos_en_red

MAX_PUNTOS = 60
MAX_SALTO_M = 8000.0     # más que esto entre dos clics, casi seguro es un error


class TrazadoInvalido(Exception):
    pass


def _pesos(ctx: GraphContext) -> RoutingWeights:
    g = ctx.graph
    ok = aristas_corribles(ctx)
    return RoutingWeights(allowed_edges=ok, weight=g.elen.astype(np.float32),
                          min_ratio=1.0, max_ratio=1.0)


def trazar(ctx: GraphContext, puntos: list[tuple[float, float]], volver: bool) -> dict:
    if len(puntos) < 2:
        raise TrazadoInvalido(tr("Marca al menos dos puntos.", "Mark at least two points."))
    if len(puntos) > MAX_PUNTOS:
        raise TrazadoInvalido(tr(f"Como máximo {MAX_PUNTOS} puntos.",
                                 f"At most {MAX_PUNTOS} points."))
    g = ctx.graph
    w = _pesos(ctx)
    red = nodos_en_red(ctx)
    nodos = [int(g.nearest_node(la, lo, mask=red)) for la, lo in puntos]
    for (la, lo), n in zip(puntos, nodos):
        x, y = g.project(la, lo)
        if np.hypot(g.x[n] - x, g.y[n] - y) > 300:
            raise TrazadoInvalido(tr("Hay un punto lejos de cualquier calle de este mapa.",
                                     "A point is far from any street on this map."))
    secuencia = nodos + ([nodos[0]] if volver and nodos[-1] != nodos[0] else [])

    tramos, aristas = [], []
    for k, (a, b) in enumerate(zip(secuencia, secuencia[1:])):
        if a == b:
            continue
        leg = ctx.provider.shortest_path(a, b, w, max_distance_m=MAX_SALTO_M * 1.6)
        if leg is None or not leg.ok:
            fin = k + 2 if k + 2 <= len(nodos) else 1
            raise TrazadoInvalido(tr(f"No hay camino corrible entre el punto {k + 1} y el {fin}.",
                                     f"There's no runnable path between point {k + 1} and {fin}."))
        tramos.append({"tipo": "trazado", "numero": k + 1,
                       "distancia_m": round(leg.distance_m),
                       "coordenadas": _coords(ctx, leg.edges)})
        aristas.extend(leg.edges)

    med = medir(ctx, aristas, 60.0)
    info = _resumen_medida(med) | {"calles": _calles_trote(ctx, aristas)}
    return {
        "comuna": ctx.region_id,
        "puntos": [[round(float(g.lat[n]), 6), round(float(g.lon[n]), 6)] for n in nodos],
        "volver": volver,
        "recorrido": info,
        "partes": tramos,
        "marcas": _marcas(ctx, aristas, med),
        "totales": {"distancia_m": round(med.distancia_m)},
        "avisos": [aviso_srtm(ctx)],
    }
