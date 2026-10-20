"""Lo que importa para entrenar, medido sobre un recorrido del grafo.

Un recorrido es la lista de aristas dirigidas que lo forman (y sus nodos). Las
detenciones se cuentan como las vive el corredor: un semáforo grande son varios
nodos del grafo a pocos metros, y es UNA detención.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from raceengine_engine.context import GraphContext
from raceengine_geodata import ALL_CLASSES

from . import nombres as _nombres
from .parques import en_parque
from .sesion import COSTE_CRUCE, CRUCE_MAYOR, CRUCE_MENOR
from .tramos import semaforos_cercanos

AGRUPAR_M = 40.0    # nodos marcados a menos de esto son la misma detención
UMBRAL_DESNIVEL_M = 2.0


def desnivel(z, umbral: float = UMBRAL_DESNIVEL_M) -> tuple[float, float]:
    """(subida, bajada) contando sólo cambios de más de `umbral` metros.

    Con un nodo cada ~10 m, sumar cada diferencia acumula el ruido del modelo
    de elevación (SRTM, ±2–3 m) como si fueran repechos. Así lo cuentan también
    los relojes GPS y Strava, y los números se parecen a los del final del día."""
    z = list(z)
    if len(z) < 2:
        return 0.0, 0.0
    sube = baja = 0.0
    ref = z[0]                  # último nivel confirmado
    for v in z[1:]:
        if v - ref >= umbral:
            sube += v - ref
            ref = v
        elif ref - v >= umbral:
            baja += ref - v
            ref = v
    return sube, baja

_COSTE = np.array([COSTE_CRUCE.get(c, 0.0) for c in ALL_CLASSES], dtype=np.float32)


@dataclass
class Medida:
    distancia_m: float
    cum: np.ndarray                         # metros acumulados por nodo
    semaforos: list[float] = field(default_factory=list)       # posiciones (m)
    cruces: list[tuple[float, float]] = field(default_factory=list)  # (pos, coste)
    subida_m: float = 0.0
    bajada_m: float = 0.0
    pendiente_max_pct: float = 0.0
    parque: float = 0.0
    sin_pavimento: float = 0.0
    repetido: float = 0.0                   # fracción de metros que se repiten
    vueltas_en_u: int = 0
    agua: list[tuple[float, str]] = field(default_factory=list)  # (pos, tipo)
    aristas: np.ndarray | None = None

    @property
    def cruces_mayores(self) -> int:
        return sum(1 for _, c in self.cruces if c >= CRUCE_MAYOR)

    @property
    def cruces_menores(self) -> int:
        return sum(1 for _, c in self.cruces if CRUCE_MENOR <= c < CRUCE_MAYOR)

    def detenciones(self, desde: float = 0.0, hasta: float | None = None) -> float:
        """Detenciones esperadas entre dos posiciones: un semáforo se encuentra
        en rojo más o menos la mitad de las veces; un cruce de avenida obliga a
        frenar a menudo; uno residencial, a veces."""
        hasta = self.distancia_m if hasta is None else hasta
        s = sum(0.6 for p in self.semaforos if desde <= p <= hasta)
        s += sum(0.5 if c >= CRUCE_MAYOR else 0.2 for p, c in self.cruces
                 if desde <= p <= hasta and c >= CRUCE_MENOR)
        return s


def _agrupar(pos: list[float]) -> list[float]:
    out: list[float] = []
    for p in pos:
        if not out or p - out[-1] > AGRUPAR_M:
            out.append(p)
    return out


def medir(ctx: GraphContext, aristas: list[int], radio_agua_m: float = 60.0) -> Medida:
    g = ctx.graph
    a = np.asarray(aristas, dtype=np.int64)
    if len(a) == 0:
        return Medida(distancia_m=0.0, cum=np.zeros(1))
    nodos = np.concatenate([[ctx.esrc[a[0]]], g.indices[a]]).astype(np.int64)
    largo = g.elen[a].astype(np.float64)
    cum = np.concatenate([[0.0], np.cumsum(largo)])
    total = float(cum[-1])
    m = Medida(distancia_m=total, cum=cum, aristas=a)

    sem = semaforos_cercanos(ctx)
    m.semaforos = _agrupar([float(cum[i]) for i in np.nonzero(sem[nodos])[0]])

    # Cruces: en cada nodo, las vías de coches que llegan y no son ni la de
    # entrada ni la de salida.
    rev = g.reverse
    cruces = []
    for i in range(1, len(nodos) - 1):
        v = nodos[i]
        if g.degree[v] < 3 or sem[v]:
            continue
        ent, sal = rev[a[i - 1]], a[i]
        es = np.arange(g.indptr[v], g.indptr[v + 1])
        es = es[(es != ent) & (es != sal) & (g.eclass[es] >= 0)]
        if len(es):
            c = float(_COSTE[g.eclass[es]].max())
            if c > 0:
                cruces.append((float(cum[i]), c))
    agrupados: list[tuple[float, float]] = []
    for p, c in cruces:
        if agrupados and p - agrupados[-1][0] <= AGRUPAR_M:
            agrupados[-1] = (agrupados[-1][0], max(agrupados[-1][1], c))
        else:
            agrupados.append((p, c))
    m.cruces = agrupados

    z = g.elev[nodos].astype(np.float64)
    m.subida_m, m.bajada_m = desnivel(z)
    if total > 100:
        d = np.arange(0.0, total, 25.0)
        zi = np.interp(d, cum, z)
        k = 4                                         # 100 m
        if len(zi) > k:
            m.pendiente_max_pct = float(np.max(np.abs(zi[k:] - zi[:-k])) / (25.0 * k) * 100)
    m.parque = float((largo * en_parque(ctx)[a]).sum() / max(total, 1.0))
    m.sin_pavimento = float((largo * ~g.epaved[a]).sum() / max(total, 1.0))

    clave = np.minimum(a, rev[a])
    _, inv, cuenta = np.unique(clave, return_inverse=True, return_counts=True)
    m.repetido = float((largo * (cuenta[inv] > 1)).sum() / max(total, 1.0))
    m.vueltas_en_u = int(np.sum(a[1:] == rev[a[:-1]]))

    tabla = _nombres.tabla(ctx)
    if tabla is not None and radio_agua_m > 0 and tabla.puntos:
        xs, ys = g.x[nodos], g.y[nodos]
        for tipo, lat, lon in tabla.puntos:
            px, py = g.project(lat, lon)
            d2 = (xs - px) ** 2 + (ys - py) ** 2
            i = int(np.argmin(d2))
            if d2[i] <= radio_agua_m ** 2:
                m.agua.append((float(cum[i]), tipo))
        m.agua.sort()
    return m
