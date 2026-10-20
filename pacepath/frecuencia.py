"""Por dónde has corrido: trazados (tus carreras, tus tramos favoritos) → aristas.

Es un *map-matching* geométrico y rápido, pensado para muchas carreras a la vez:

  1. Las aristas corribles del grafo se muestrean cada 5 m y van a un KD-tree
     (una vez por versión del grafo).
  2. Cada trazado se densifica cada 8 m y cada punto se asigna a la arista más
     cercana a menos de 20 m.
  3. Cada carrera cuenta UNA vez por arista, aunque pase varias veces.

Strava da la polilínea resumida de cada actividad (simplificada, puntos cada
decenas de metros en las rectas): densificar la recorre igual por la calle.

Nada se guarda en el servidor: el resultado vuelve al teléfono del corredor,
que lo adjunta cuando pide una ruta (términos de la API de Strava).
"""
from __future__ import annotations

import math
import threading

import numpy as np
from scipy.spatial import cKDTree

from raceengine_engine.context import GraphContext

from .tramos import aristas_corribles

PASO_GRAFO_M = 5.0
PASO_TRAZADO_M = 8.0
RADIO_M = 20.0
MAX_ARISTAS = 30_000

_lock = threading.Lock()
_ARBOL: dict[str, tuple[cKDTree, np.ndarray]] = {}


def _arbol(ctx: GraphContext) -> tuple[cKDTree, np.ndarray]:
    """KD-tree de puntos sobre las aristas corribles (sentido canónico u<v)."""
    with _lock:
        if ctx.version in _ARBOL:
            return _ARBOL[ctx.version]
        g = ctx.graph
        rev = g.reverse
        e = np.nonzero(aristas_corribles(ctx))[0]
        e = e[e <= rev[e]]                                  # una por par de sentidos
        n = np.maximum(1, np.ceil(g.elen[e] / PASO_GRAFO_M).astype(np.int64))
        ids = np.repeat(e, n)
        # fracción a lo largo de la arista: (i + 0,5) / n
        inicio = np.repeat(np.cumsum(n) - n, n)
        t = (np.arange(len(ids)) - inicio + 0.5) / np.repeat(n, n)
        u, v = ctx.esrc[ids], g.indices[ids]
        x = g.x[u] + t * (g.x[v] - g.x[u])
        y = g.y[u] + t * (g.y[v] - g.y[u])
        arbol = cKDTree(np.column_stack([x, y]))
        _ARBOL.clear()
        _ARBOL[ctx.version] = (arbol, ids)
        return _ARBOL[ctx.version]


def decodificar(s: str) -> list[tuple[float, float]]:
    """Polilínea codificada de Google (la de Strava) → [(lat, lon)]."""
    pts, i, lat, lon = [], 0, 0, 0
    while i < len(s):
        for k in (0, 1):
            res, shift = 0, 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(res >> 1) if res & 1 else res >> 1
            if k == 0:
                lat += d
            else:
                lon += d
        pts.append((lat / 1e5, lon / 1e5))
    return pts


def _densificar(ctx: GraphContext, puntos) -> np.ndarray:
    g = ctx.graph
    xy = np.array([g.project(la, lo) for la, lo in puntos], dtype=np.float64)
    if len(xy) < 2:
        return xy
    out = [xy[:1]]
    for a, b in zip(xy[:-1], xy[1:]):
        d = float(np.hypot(*(b - a)))
        n = max(1, int(math.ceil(d / PASO_TRAZADO_M)))
        t = (np.arange(1, n + 1) / n)[:, None]
        out.append(a + t * (b - a))
    return np.vstack(out)


def aristas_de(ctx: GraphContext, puntos) -> np.ndarray:
    """Aristas (sentido canónico) por las que pasa un trazado."""
    arbol, ids = _arbol(ctx)
    xy = _densificar(ctx, puntos)
    if not len(xy):
        return np.zeros(0, dtype=np.int64)
    dist, idx = arbol.query(xy, distance_upper_bound=RADIO_M)
    ok = np.isfinite(dist)
    return np.unique(ids[idx[ok]])


def frecuencia(ctx: GraphContext, trazados) -> tuple[np.ndarray, np.ndarray]:
    """(aristas, veces): cuántos trazados pasan por cada arista."""
    cuenta: dict[int, int] = {}
    for puntos in trazados:
        for e in aristas_de(ctx, puntos).tolist():
            cuenta[e] = cuenta.get(e, 0) + 1
    if not cuenta:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    a = np.fromiter(cuenta.keys(), dtype=np.int64, count=len(cuenta))
    v = np.fromiter(cuenta.values(), dtype=np.int64, count=len(cuenta))
    return a, v


def mascara(ctx: GraphContext, aristas) -> np.ndarray:
    """bool[E] en ambos sentidos a partir de aristas canónicas."""
    g = ctx.graph
    m = np.zeros(g.n_edges, dtype=bool)
    a = np.asarray([e for e in aristas if 0 <= e < g.n_edges], dtype=np.int64)
    if len(a):
        m[a] = True
        m[g.reverse[a]] = True
    return m
