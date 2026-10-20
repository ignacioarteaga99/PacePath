"""Tramos rectos del grafo: la materia prima de las series.

Una repetición necesita una línea por la que correr sin pensar: sin detenerse,
sin girar, sin cambiar de ritmo por una cuesta. El grafo de RaceEngine no tiene
«calles», tiene aristas cortas (mediana ~10 m: cada nodo de geometría de OSM es
un nodo del grafo), así que primero hay que reconstruir las líneas.

Se hace como en cartografía con los *strokes*: en cada nodo se emparejan las
aristas que siguen «de frente» (desviación < 35°), siempre la pareja más recta
primero. Encadenando parejas salen trazos largos y continuos —una avenida, la
senda central de un parque— que no se cortan porque cambie la clase de la vía o
el nombre de la calle, sólo porque deja de ir recto.

Sobre cada trazo, cada nodo lleva lo que importa para una serie:
  · `semaforo`: semáforo en el nodo, o a menos de 25 m de un cruce (en OSM el
    paso de peatones de una senda queda a unos metros del nodo del semáforo).
  · `cruce`: coste de cruzar las otras vías que llegan al nodo (ver
    `sesion.COSTE_CRUCE`): una senda no cuenta, una avenida mucho.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from raceengine_contracts import DISCIPLINE_CLASSES
from raceengine_engine.context import GraphContext
from raceengine_geodata import ALL_CLASSES

from .sesion import COSTE_CRUCE

DESVIO_MAX_RAD = math.radians(35.0)
RADIO_SEMAFORO_M = 25.0
LARGO_MIN_TRAZO_M = 150.0

_IDX = {c: i for i, c in enumerate(ALL_CLASSES)}
CLASES_CORRER = np.array([_IDX[c] for c in DISCIPLINE_CLASSES["running"]], dtype=np.int8)
# Sendas que sólo se usan dentro de un parque: fuera de él suelen ser veredas
# junto a la calzada, que el grafo ya representa con la propia calle.
CLASES_SENDA = np.array([_IDX[c] for c in ("footway", "path", "cycleway", "track")],
                        dtype=np.int8)
_COSTE_POR_CLASE = np.array([COSTE_CRUCE.get(c, 0.0) for c in ALL_CLASSES],
                            dtype=np.float32)


def aristas_corribles(ctx: GraphContext, sendas_de_parque: bool = True) -> np.ndarray:
    """bool[E] — aristas por las que puede ir una repetición o un trote."""
    g = ctx.graph
    ok = np.isin(g.eclass, CLASES_CORRER)
    if sendas_de_parque:
        from .parques import en_parque   # evita import circular con nombres
        ok |= np.isin(g.eclass, CLASES_SENDA) & en_parque(ctx)
    return ok & ~g.eprivate & ~g.etunnel & (g.elen > 0)


@dataclass
class Trazo:
    nodos: np.ndarray        # int64[k+1]
    aristas: np.ndarray      # int64[k] aristas dirigidas en el sentido del trazo
    cum: np.ndarray          # float64[k+1] metros acumulados
    semaforo: np.ndarray     # bool[k+1]
    cruce: np.ndarray        # float32[k+1] coste de cruce (0 en los extremos)

    @property
    def largo_m(self) -> float:
        return float(self.cum[-1])


def _rumbo(ctx: GraphContext) -> np.ndarray:
    g = ctx.graph
    d = g.indices
    return np.arctan2(g.y[d] - g.y[ctx.esrc], g.x[d] - g.x[ctx.esrc])


def _desvio(h1: np.ndarray | float, h2: np.ndarray | float):
    """Cuánto se aparta de «seguir de frente» entrar por una arista y salir por
    otra, dadas las dos direcciones SALIENTES del nodo (de frente = opuestas)."""
    d = np.abs((np.asarray(h1) - np.asarray(h2) + np.pi) % (2 * np.pi) - np.pi)
    return np.pi - d


def _emparejar(ctx: GraphContext, ok: np.ndarray, rumbo: np.ndarray) -> np.ndarray:
    """int64[E] — para cada arista saliente de un nodo, la otra arista saliente
    del mismo nodo con la que forma la continuación más recta (−1 si ninguna)."""
    g = ctx.graph
    pareja = np.full(g.n_edges, -1, dtype=np.int64)
    salientes = np.bincount(ctx.esrc[ok], minlength=g.n_nodes)

    # Nodos con exactamente dos salidas (la inmensa mayoría: geometría): vectorizado.
    e_ok = np.nonzero(ok)[0]
    dos = salientes[ctx.esrc[e_ok]] == 2
    e2 = e_ok[dos]
    if len(e2):
        # CSR ordenado por origen ⇒ las dos salidas de un nodo quedan contiguas.
        a, b = e2[0::2], e2[1::2]
        recto = _desvio(rumbo[a], rumbo[b]) < DESVIO_MAX_RAD
        pareja[a[recto]] = b[recto]
        pareja[b[recto]] = a[recto]

    # Cruces: emparejamiento voraz, la pareja más recta primero.
    for v in np.nonzero(salientes >= 3)[0]:
        es = [e for e in range(g.indptr[v], g.indptr[v + 1]) if ok[e]]
        pares = sorted(
            (float(_desvio(rumbo[a], rumbo[b])), a, b)
            for i, a in enumerate(es) for b in es[i + 1:]
        )
        usado = set()
        for dv, a, b in pares:
            if dv >= DESVIO_MAX_RAD:
                break
            if a in usado or b in usado:
                continue
            pareja[a], pareja[b] = b, a
            usado.update((a, b))
    return pareja


_SEMAFOROS: dict[str, np.ndarray] = {}


def semaforos_cercanos(ctx: GraphContext) -> np.ndarray:
    """bool[N] — semáforo en el nodo, o cruce a menos de 25 m de uno."""
    if ctx.version not in _SEMAFOROS:
        _SEMAFOROS.clear()
        _SEMAFOROS[ctx.version] = _semaforos_cercanos(ctx)
    return _SEMAFOROS[ctx.version]


def _semaforos_cercanos(ctx: GraphContext) -> np.ndarray:
    g = ctx.graph
    sem = g.is_signal.copy()
    cruce = g.degree >= 3
    for s in np.nonzero(g.is_signal)[0]:
        cerca = g.grid.within(float(g.x[s]), float(g.y[s]), RADIO_SEMAFORO_M)
        sem[cerca[cruce[cerca]]] = True
    return sem


def _coste_cruce(ctx: GraphContext, nodo: int, entra: int, sale: int) -> float:
    """Coste de las vías que cruzan el trazo en `nodo` (se excluyen las dos
    aristas del propio trazo)."""
    g = ctx.graph
    rev = g.reverse
    otras = [e for e in range(g.indptr[nodo], g.indptr[nodo + 1])
             if e != sale and e != rev[entra] and g.eclass[e] >= 0]
    if not otras:
        return 0.0
    return float(_COSTE_POR_CLASE[g.eclass[otras]].max())


def construir_trazos(ctx: GraphContext, sendas_de_parque: bool = True) -> list[Trazo]:
    g = ctx.graph
    ok = aristas_corribles(ctx, sendas_de_parque)
    rumbo = _rumbo(ctx)
    pareja = _emparejar(ctx, ok, rumbo)
    rev = g.reverse
    semaforo = semaforos_cercanos(ctx)
    grado = g.degree

    visto = np.zeros(g.n_edges, dtype=bool)
    trazos: list[Trazo] = []

    def siguiente(e: int) -> int:
        p = pareja[rev[e]]
        return int(p) if p >= 0 and ok[p] else -1

    def anterior(e: int) -> int:
        p = pareja[e]
        return int(rev[p]) if p >= 0 and ok[rev[p]] else -1

    for e0 in np.nonzero(ok)[0]:
        if visto[e0]:
            continue
        # Retroceder hasta la cabeza del trazo (o dar la vuelta, si es un anillo).
        cabeza, e = int(e0), anterior(int(e0))
        while e >= 0 and e != e0 and not visto[e]:
            cabeza, e = e, anterior(e)
        aristas = []
        e = cabeza
        while e >= 0 and not visto[e]:
            aristas.append(e)
            visto[e] = visto[rev[e]] = True
            e = siguiente(e)
        aristas = np.asarray(aristas, dtype=np.int64)
        largo = float(g.elen[aristas].sum())
        if largo < LARGO_MIN_TRAZO_M:
            continue
        nodos = np.concatenate([[ctx.esrc[aristas[0]]], g.indices[aristas]]).astype(np.int64)
        cum = np.concatenate([[0.0], np.cumsum(g.elen[aristas], dtype=np.float64)])
        cruce = np.zeros(len(nodos), dtype=np.float32)
        for i in range(1, len(nodos) - 1):
            if grado[nodos[i]] >= 3:
                cruce[i] = _coste_cruce(ctx, int(nodos[i]), int(aristas[i - 1]),
                                        int(aristas[i]))
        trazos.append(Trazo(nodos=nodos, aristas=aristas, cum=cum,
                            semaforo=semaforo[nodos], cruce=cruce))
    return trazos


_CACHE: dict[tuple[str, bool], list[Trazo]] = {}


def trazos(ctx: GraphContext, sendas_de_parque: bool = True) -> list[Trazo]:
    """Trazos de la comuna, calculados una vez por versión del grafo."""
    clave = (ctx.version, sendas_de_parque)
    if clave not in _CACHE:
        # Sólo una comuna en memoria a la vez (Render gratis): lo mismo aquí.
        _CACHE.clear()
        _CACHE[clave] = construir_trazos(ctx, sendas_de_parque)
    return _CACHE[clave]
