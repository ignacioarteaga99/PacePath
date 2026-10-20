"""Qué aristas están dentro de un parque.

El grafo de RaceEngine trae `epark`, pero marca muy poco (814 de 140.000 sendas
en Providencia): las sendas de Parque Balmaceda o Bustamante quedaban fuera, y
con ellas los mejores sitios para correr. Aquí se completa con los polígonos de
parques y jardines que publica el workflow «Nombres de calle».
"""
from __future__ import annotations

import threading

import numpy as np

from raceengine_engine.context import GraphContext

from . import nombres as _nombres

_lock = threading.Lock()
_CACHE: dict[str, np.ndarray] = {}


def _nodos_en(lat: np.ndarray, lon: np.ndarray, poli: np.ndarray) -> np.ndarray:
    """Ray casting vectorizado sobre los vértices del polígono."""
    py, px = poli[:, 0], poli[:, 1]
    dentro = np.zeros(len(lat), dtype=bool)
    j = len(poli) - 1
    for i in range(len(poli)):
        cruza = (py[i] > lat) != (py[j] > lat)
        with np.errstate(divide="ignore", invalid="ignore"):
            xc = (px[j] - px[i]) * (lat - py[i]) / (py[j] - py[i]) + px[i]
        dentro ^= cruza & (lon < xc)
        j = i
    return dentro


def en_parque(ctx: GraphContext) -> np.ndarray:
    """bool[E] — la arista está en un parque (ambos extremos dentro)."""
    with _lock:
        if ctx.version in _CACHE:
            return _CACHE[ctx.version]
        g = ctx.graph
        nodo = np.zeros(g.n_nodes, dtype=bool)
        tabla = _nombres.tabla(ctx)
        for poli in (tabla.parques if tabla else []):
            p = np.asarray(poli, dtype=np.float64)
            caja = ((g.lat >= p[:, 0].min()) & (g.lat <= p[:, 0].max())
                    & (g.lon >= p[:, 1].min()) & (g.lon <= p[:, 1].max()))
            idx = np.nonzero(caja)[0]
            if len(idx):
                nodo[idx[_nodos_en(g.lat[idx], g.lon[idx], p)]] = True
        mascara = g.epark | (nodo[ctx.esrc] & nodo[g.indices])
        _CACHE.clear()
        _CACHE[ctx.version] = mascara
        return mascara
