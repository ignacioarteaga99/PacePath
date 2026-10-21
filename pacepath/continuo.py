"""Sesiones continuas: tempo, fondo largo y regenerativo.

Fondo y regenerativo son un circuito desde casa de la distancia pedida. Los
genera el motor de RaceEngine (`generate_loops` + `adjust_distance`), pero con
pesos propios de la sesión (`pesos_sesion`) y elegidos por una puntuación de
entrenamiento, no de carrera: detenciones, parque, subida, fuentes de agua.

El tempo es distinto: lo que importa es el bloque continuo, no el recorrido
entero. Se comparan dos formas de hacerlo y gana la que tiene menos detenciones:

  · corredor: el tramo más largo sin semáforos ni cruces cerca de casa, en ida
    y vuelta tantas veces como haga falta (cada vuelta en U cuenta un poco);
  · circuito: un circuito de calentamiento + bloque + vuelta, y el bloque va en
    la ventana del circuito con menos detenciones.
"""
from __future__ import annotations

import dataclasses
import hashlib

import numpy as np

from raceengine_contracts import ConstraintModel, GeoPoint
from raceengine_geodata import ALL_CLASSES
from raceengine_contracts.constraints import DistanceLayer, GeometryLayer
from raceengine_engine.context import GraphContext
from raceengine_engine.generate import generate_loops, generate_open_routes
from raceengine_engine.repair import adjust_distance
from raceengine_routing import RoutingWeights
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from .idioma import tr
from .explicacion import explicar_continuo, explicar_tempo
from .metricas import Medida, desnivel, medir
from .sesion import COSTE_CRUCE, CRUCE_MENOR, Requisitos, Sesion, TipoSesion, requisitos
from .series import (
    SinTramo, aviso_srtm, _calles_trote, _tramo_info, _tramo_trote, base_plan,
    buscar_tramos, dentro_radio, nodo_salida, pesos_trote, radio_actual, tramo_final,
)
from .parques import en_parque
from .tramos import aristas_corribles, semaforos_cercanos

_IDX_PEATONAL = ALL_CLASSES.index("pedestrian")
_COSTE = np.array([COSTE_CRUCE.get(c, 0.0) for c in ALL_CLASSES], dtype=np.float32)

# Una vuelta en U en el corredor cuesta como un cruce residencial: frenar,
# girar, volver a coger el ritmo.
DETENCION_VUELTA_U = 0.2
CORREDOR_MIN_M = 800.0
RODEO = 0.82           # largo real ≈ largo del anillo / RODEO con estos pesos


# ── Pesos ───────────────────────────────────────────────────────────────────

def _coste_cruce_nodo(ctx: GraphContext) -> tuple[np.ndarray, np.ndarray]:
    """(vías de coches por nodo, coste máximo de esas vías)."""
    g = ctx.graph
    coche = _COSTE[g.eclass] >= CRUCE_MENOR
    grado = np.bincount(ctx.esrc[coche], minlength=g.n_nodes)
    maximo = np.zeros(g.n_nodes, dtype=np.float32)
    np.maximum.at(maximo, ctx.esrc[coche], _COSTE[g.eclass[coche]])
    return grado, maximo


def pesos_sesion(ctx: GraphContext, casa: int, radio_m: float,
                 req: Requisitos) -> RoutingWeights:
    """Los pesos de trote más los multiplicadores de la sesión."""
    g = ctx.graph
    base = pesos_trote(ctx, casa, radio_m)
    dst = g.indices
    mult = np.ones(g.n_edges, dtype=np.float32)

    extra = np.zeros(g.n_edges, dtype=np.float32)
    extra[semaforos_cercanos(ctx)[dst]] += np.float32(req.penal_semaforo_m)
    if req.penal_cruce_m:
        grado, maximo = _coste_cruce_nodo(ctx)
        en_coche = _COSTE[g.eclass] >= CRUCE_MENOR
        # Por la calle: es cruce si llega una tercera vía. Por una senda: basta
        # con que pase una calle.
        cruza = np.where(en_coche, grado[dst] >= 3, grado[dst] >= 2)
        extra += (req.penal_cruce_m * np.where(cruza, maximo[dst], 0)).astype(np.float32)
    if req.penal_subida_m_por_m:
        subida = np.maximum(g.egrad, 0) * g.elen / 100.0           # metros que se suben
        extra += (req.penal_subida_m_por_m * subida).astype(np.float32)
    mult[en_parque(ctx)] *= np.float32(req.mult_parque)
    mult[~g.epaved] *= np.float32(req.mult_sin_pavimento)
    if req.preferidas is not None:
        mult *= req.preferidas
    if req.mult_subida:
        mult *= (1.0 + req.mult_subida * np.maximum(g.egrad, 0)).astype(np.float32)

    weight = (base.weight * mult + extra).astype(np.float32)
    ok = base.allowed_edges & (g.elen > 0)
    ratio = weight[ok] / g.elen[ok]
    return RoutingWeights(allowed_edges=base.allowed_edges, weight=weight,
                          min_ratio=max(float(ratio.min()), 0.05),
                          max_ratio=max(float(ratio.max()), 1.0))


# ── Circuitos ───────────────────────────────────────────────────────────────

def fraccion_preferida(med: Medida, req: Requisitos) -> float:
    """Qué parte del recorrido va por calles de siempre o tramos favoritos."""
    if req.preferidas is None or med.aristas is None or not len(med.aristas):
        return 0.0
    total = float(med.cum[-1])
    if total <= 0:
        return 0.0
    return float((np.diff(med.cum) * (req.preferidas[med.aristas] < 1)).sum() / total)


def puntuar(med: Medida, req: Requisitos, objetivo_m: float) -> float:
    """Puntos de penalización por km (menos es mejor)."""
    km = max(med.distancia_m / 1000, 0.1)
    # Cada vuelta en U (ida y vuelta, idas por un corredor) frena como un cruce.
    p = req.peso_detencion * (med.detenciones() + DETENCION_VUELTA_U * med.vueltas_en_u) / km
    p += req.peso_subida * med.subida_m / km
    p += req.peso_exceso_subida * max(0.0, med.subida_m / km - req.subida_tolerada_m_km)
    if req.max_subida_m is not None and med.subida_m > req.max_subida_m:
        # Tope duro: cualquier candidato que lo cumpla gana a uno que no.
        p += 1000.0 + 20.0 * (med.subida_m - req.max_subida_m)
    p -= req.peso_parque * med.parque
    p -= req.peso_preferida * fraccion_preferida(med, req)
    # Repetir calle no es grave en un fondo (ida y vuelta es un clásico).
    p += (8.0 if req.tipo is TipoSesion.FONDO else 25.0) * med.repetido
    if req.radio_agua_m and med.agua:
        p -= 8.0 + 2.0 * min(len(med.agua) - 1, 3)
    # Error de distancia: fuera del ±3 % se paga caro.
    err = abs(med.distancia_m - objetivo_m) / objetivo_m
    p += 400.0 * max(0.0, err - 0.03) + 30.0 * err
    return p


def _semilla(casa: int, distancia: float, tipo: str) -> np.random.Generator:
    h = hashlib.sha256(f"{casa}:{distancia:.0f}:{tipo}".encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "little"))


def circuito(ctx: GraphContext, casa: int, distancia_m: float, req: Requisitos,
             k: int = 4, afinar: int = 2,
             con_ida_y_vuelta: bool = True) -> tuple[list[int], Medida, list[tuple]]:
    """El mejor circuito desde `casa` de `distancia_m` para estos requisitos.

    Devuelve (aristas, medida, otros candidatos [(aristas, medida, puntos)])."""
    g = ctx.graph
    w = pesos_sesion(ctx, casa, distancia_m * 0.6 + 1500, req)
    modelo = ConstraintModel(
        region=ctx.region_id,
        start=GeoPoint(lat=float(g.lat[casa]), lon=float(g.lon[casa])),
        distance=DistanceLayer(target_m=distancia_m, tolerance_pct=2.0),
        geometry=GeometryLayer(smoothness=0.6,
                               search_radius_max_m=_radio_busqueda(distancia_m)),
    )
    reach = ctx.provider.reachable(casa, w, distancia_m * 0.6)
    rng = _semilla(casa, distancia_m, req.tipo.value)
    # Con detenciones caras los tramos dan rodeos y el circuito sale más largo
    # que el anillo de anclas: se genera para un objetivo menor y el ajuste de
    # distancia lo lleva al real. Sin esto el motor descartaba casi todos los
    # circuitos por largos y agotaba los intentos (13 s en un fondo de 18 km).
    gen = modelo.model_copy(update={"distance": DistanceLayer(
        target_m=distancia_m * RODEO, tolerance_pct=2.0)})
    loops = generate_loops(ctx, gen, w, casa, reach, rng, k=k, budget_ms=1500)

    def eval_(lp) -> tuple:
        med = medir(ctx, lp.edges, req.radio_agua_m)
        return (lp, med, puntuar(med, req, distancia_m))

    crudos = sorted((eval_(lp) for lp in loops), key=lambda x: x[2])
    finos = []
    for lp, _, _ in crudos[:afinar]:
        ajustado, _ = adjust_distance(ctx, modelo, w, lp, casa, reach, {}, rng,
                                      budget_ms=600)
        finos.append(eval_(ajustado))
    todos = [(lp.edges, med, p) for lp, med, p in finos + crudos[afinar:]]
    if con_ida_y_vuelta:
        todos += ida_y_vuelta(ctx, casa, distancia_m, req, w)
        todos += por_corredor(ctx, casa, distancia_m, req, w)
    if not todos:
        raise SinTramo(tr("No encontré ningún recorrido desde esa salida.",
                          "I couldn't find any route from that start."))
    todos.sort(key=lambda x: x[2])
    return todos[0][0], todos[0][1], todos[1:]


def _radio_busqueda(distancia_m: float) -> float:
    """Hasta dónde busca anclas el motor: lo de siempre, o el radio máximo."""
    r = distancia_m * 0.6 + 1500
    # El motor exige ≥ 500 m; con un radio menor, los pesos igual cortan en el radio.
    return max(500.0, min(r, radio_actual())) if radio_actual() is not None else r


def _arbol(ctx: GraphContext, w: RoutingWeights, origen: int, limite: float):
    """Caminos de coste mínimo desde `origen` a todos los nodos, de una vez
    (Dijkstra de scipy, en C). Devuelve (coste, predecesor)."""
    g = ctx.graph
    ok = w.allowed_edges & (g.elen > 0)
    cuenta = np.bincount(ctx.esrc[ok], minlength=g.n_nodes)
    indptr = np.concatenate([[0], np.cumsum(cuenta)])
    m = csr_matrix((w.weight[ok].astype(np.float64), g.indices[ok], indptr),
                   shape=(g.n_nodes, g.n_nodes))
    coste, pred = dijkstra(m, indices=origen, return_predecessors=True, limit=limite)
    return coste, pred


def _arista(ctx: GraphContext, w: RoutingWeights, u: int, v: int) -> int:
    """La arista u→v permitida más barata."""
    g = ctx.graph
    es = np.arange(g.indptr[u], g.indptr[u + 1])
    es = es[(g.indices[es] == v) & w.allowed_edges[es]]
    return int(es[np.argmin(w.weight[es])])


def _metros_arbol(ctx: GraphContext, coste: np.ndarray, pred: np.ndarray,
                  origen: int) -> np.ndarray:
    """Metros reales desde `origen` siguiendo el árbol de coste mínimo (que no
    es el del camino más corto: el árbol esquiva semáforos)."""
    g = ctx.graph
    orden = np.argsort(coste)
    orden = orden[np.isfinite(coste[orden])]
    metros = np.full(g.n_nodes, np.inf)
    metros[origen] = 0.0
    for v in orden[1:]:
        u = pred[v]
        if u >= 0 and np.isfinite(metros[u]):
            metros[v] = metros[u] + float(np.hypot(g.x[v] - g.x[u], g.y[v] - g.y[u]))
    return metros


def _camino(ctx: GraphContext, w: RoutingWeights, pred: np.ndarray, origen: int,
            destino: int) -> list[int]:
    nodos = [destino]
    while nodos[-1] != origen:
        nodos.append(int(pred[nodos[-1]]))
    nodos.reverse()
    return [_arista(ctx, w, a, b) for a, b in zip(nodos, nodos[1:])]


def _por_sector(ctx: GraphContext, casa: int, cand: np.ndarray, coste: np.ndarray,
                n: int) -> list[int]:
    """Un destino por rumbo: el más barato de cada sector."""
    g = ctx.graph
    ang = np.arctan2(g.y[cand] - g.y[casa], g.x[cand] - g.x[casa])
    sector = ((ang + np.pi) / (2 * np.pi) * n).astype(int) % n
    return [int(cand[sector == k][np.argmin(coste[cand[sector == k]])])
            for k in range(n) if (sector == k).any()]


def solo_ida(ctx: GraphContext, casa: int, distancia_m: float, req: Requisitos,
             w: RoutingWeights, n: int = 24) -> list[tuple[list[int], Medida, float]]:
    """Sin meta fija: termina donde se cumplen los km, por el camino más limpio
    (para volver en metro, o que te pasen a buscar)."""
    coste, pred = _arbol(ctx, w, casa, distancia_m * 6)
    metros = _metros_arbol(ctx, coste, pred, casa)
    cand = np.nonzero((metros >= 0.96 * distancia_m) & (metros <= 1.04 * distancia_m))[0]
    out = []
    for destino in (_por_sector(ctx, casa, cand, coste, n) if len(cand) else []):
        aristas = _camino(ctx, w, pred, casa, destino)
        med = medir(ctx, aristas, req.radio_agua_m)
        out.append((aristas, med, puntuar(med, req, distancia_m)))
    return out


def hasta_meta(ctx: GraphContext, casa: int, meta: int, distancia_m: float,
               req: Requisitos, w: RoutingWeights) -> list[tuple[list[int], Medida, float]]:
    """De la salida a la meta midiendo `distancia_m`: arcos del motor de RaceEngine
    (`generate_open_routes`) ajustados a la distancia, más el camino directo si
    ya mide lo pedido."""
    g = ctx.graph
    directo = ctx.provider.shortest_path(casa, meta, w)
    if directo is None or not directo.ok:
        raise SinTramo(tr("No hay camino corrible entre la salida y la meta.",
                          "There is no runnable path between the start and the finish."))
    if directo.distance_m > distancia_m * 1.05:
        raise SinTramo(tr(f"La meta está a {directo.distance_m / 1000:.1f} km por calle: más "
                          f"que los {distancia_m / 1000:.0f} km de la sesión.",
                          f"The finish is {directo.distance_m / 1000:.1f} km away by road: more "
                          f"than the session's {distancia_m / 1000:.0f} km."))
    modelo = ConstraintModel(
        region=ctx.region_id,
        start=GeoPoint(lat=float(g.lat[casa]), lon=float(g.lon[casa])),
        finish=GeoPoint(lat=float(g.lat[meta]), lon=float(g.lon[meta])),
        distance=DistanceLayer(target_m=distancia_m, tolerance_pct=2.0),
        geometry=GeometryLayer(smoothness=0.6, search_radius_max_m=_radio_busqueda(distancia_m)),
    )
    reach = ctx.provider.reachable(casa, w, distancia_m * 0.7)
    rng = _semilla(casa * 7919 + meta, distancia_m, req.tipo.value)
    out = []
    if directo.distance_m >= distancia_m * 0.95:
        med = medir(ctx, directo.edges, req.radio_agua_m)
        out.append((list(directo.edges), med, puntuar(med, req, distancia_m)))
    gen = modelo.model_copy(update={"distance": DistanceLayer(
        target_m=distancia_m * RODEO, tolerance_pct=2.0)})
    for lp in generate_open_routes(ctx, gen, w, casa, meta, reach, rng, k=4, budget_ms=1500):
        ajustado, _ = adjust_distance(ctx, modelo, w, lp, casa, reach, {}, rng,
                                      budget_ms=600, end_node=meta)
        med = medir(ctx, ajustado.edges, req.radio_agua_m)
        out.append((list(ajustado.edges), med, puntuar(med, req, distancia_m)))
    return out


# ── Corredores: parques y paseos peatonales seguidos ───────────────────────

CORREDOR_MIN_M = 2000.0     # sendas totales mínimas para que valga la pena
PUENTE_MAX_M = 40.0         # cruces de calle cortos que unen un parque con el siguiente
_CORREDORES: dict[str, tuple] = {}


def corredores(ctx: GraphContext, extra: np.ndarray | None = None
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(máscara de aristas de corredor, componente por nodo, metros por componente).

    Un corredor es lo que se corre sin coches: sendas de parque y paseos
    peatonales, más los cruces de calle cortos que unen un parque con el
    siguiente (la ribera del Mapocho está partida por puentes y avenidas)."""
    if extra is None and ctx.version in _CORREDORES:
        return _CORREDORES[ctx.version]
    g = ctx.graph
    n = g.n_nodes
    corr = aristas_corribles(ctx)
    M = corr & (en_parque(ctx) | (g.eclass == _IDX_PEATONAL))
    if extra is not None:
        # Las calles de siempre y los tramos favoritos también son corredor,
        # aunque OSM no los marque como parque (la ribera de Providencia).
        M |= corr & extra
    for _ in range(2):
        tocado = np.zeros(n, dtype=bool)
        tocado[ctx.esrc[M]] = True
        M = M | (corr & (g.elen <= PUENTE_MAX_M) & (tocado[ctx.esrc] | tocado[g.indices]))
    A = csr_matrix((np.ones(int(M.sum())), (ctx.esrc[M], g.indices[M])), shape=(n, n))
    _, comp = connected_components(A, directed=False)
    metros = np.bincount(comp[ctx.esrc[M]], weights=g.elen[M].astype(np.float64)) / 2
    if extra is not None:
        return M, comp, metros
    _CORREDORES.clear()
    _CORREDORES[ctx.version] = (M, comp, metros)
    return _CORREDORES[ctx.version]


def por_corredor(ctx: GraphContext, casa: int, distancia_m: float, req: Requisitos,
                 w: RoutingWeights, max_corredores: int = 6,
                 giros: int = 8) -> list[tuple[list[int], Medida, float]]:
    """Fondos sobre corredores: trote hasta la entrada más cercana, idas y
    vueltas por el corredor hasta completar los km y vuelta a casa.

    Si el corredor alcanza, es una ida y vuelta entera por él (varios puntos de
    giro, en distintas direcciones). Si es más corto, se repite de punta a punta
    y la última pasada gira a mitad de camino para cuadrar la distancia."""
    g = ctx.graph
    M, comp, metros = corredores(
        ctx, None if req.preferidas is None else req.preferidas < 1)
    if radio_actual() is not None:
        M = M & dentro_radio(ctx, ctx.esrc) & dentro_radio(ctx, g.indices)
    coste, pred = _arbol(ctx, w, casa, distancia_m * 6)
    llegada = _metros_arbol(ctx, coste, pred, casa)
    tope_acerc = max(3000.0, 0.3 * distancia_m)

    nodos_m = np.unique(ctx.esrc[M])
    alcanzables = nodos_m[llegada[nodos_m] <= tope_acerc]
    if not len(alcanzables):
        return []
    cands = np.unique(comp[alcanzables])
    cands = [int(c) for c in cands[np.argsort(-metros[cands])] if metros[c] >= CORREDOR_MIN_M]
    # Dentro del corredor también cuesta subir (el Parque Metropolitano es parque).
    sube = np.maximum(g.egrad, 0) * g.elen / 100.0
    peso_c = g.elen + 8.0 * sube
    if req.preferidas is not None:
        peso_c = peso_c * req.preferidas
    wc = RoutingWeights(allowed_edges=M, weight=peso_c.astype(np.float32),
                        min_ratio=float(min(1.0, peso_c[M].min() / max(g.elen[M].min(), 0.01)))
                        if M.any() else 1.0, max_ratio=1.0)
    rev = g.reverse
    out = []
    for c in cands[:max_corredores]:
        en_c = alcanzables[comp[alcanzables] == c]
        E = int(en_c[np.argmin(coste[en_c])])                 # entrada más barata
        acerc = _camino(ctx, w, pred, casa, E)
        resto = distancia_m - 2 * llegada[E]
        if resto < 2000:
            continue
        cc, pc = _arbol(ctx, wc, E, resto * 6)
        mc = _metros_arbol(ctx, cc, pc, E)
        nodos_c = np.nonzero(np.isfinite(mc))[0]
        if len(nodos_c) < 2:
            continue
        # En cada dirección, el punto de giro más lejano que quepa en lo que
        # queda (a lo más la mitad: hay que volver). Si no alcanza, ese tramo se
        # repite y la última pasada gira a mitad de camino para cuadrar.
        cabe = nodos_c[mc[nodos_c] <= resto / 2 * 1.06]
        ang = np.arctan2(g.y[cabe] - g.y[E], g.x[cabe] - g.x[E])
        sector = ((ang + np.pi) / (2 * np.pi) * giros).astype(int) % giros
        for k_s in range(giros):
            en_s = cabe[sector == k_s]
            if not len(en_s):
                continue
            T = int(en_s[np.argmax(mc[en_s])])
            largo = float(mc[T])
            # Ir y volver quince veces por un parque de 400 m no es un fondo.
            if largo < max(1500.0, distancia_m / 10):
                continue
            ida = _camino(ctx, wc, pc, E, T)
            vuelta = [int(rev[e]) for e in reversed(ida)]
            k = max(1, int(resto // (2 * largo)))
            falta = max(0.0, resto - 2 * k * largo)
            parcial, acum = [], 0.0
            for e in ida:
                if acum >= falta / 2:
                    break
                parcial.append(e)
                acum += float(g.elen[e])
            tramo = (ida + vuelta) * k + parcial + [int(rev[e]) for e in reversed(parcial)]
            aristas = acerc + tramo + [int(rev[e]) for e in reversed(acerc)]
            out.append(_cand_corredor(ctx, aristas, req, distancia_m, largo, k, ida, acerc))
    return out


def _cand_corredor(ctx, aristas, req, distancia_m, largo, vueltas, corredor, acerc):
    med = medir(ctx, aristas, req.radio_agua_m)
    med.forma = "corredor"
    med.detalle = {"largo_m": round(largo), "vueltas": vueltas,
                   "acercamiento_m": round(float(ctx.graph.elen[acerc].sum())) if acerc else 0,
                   "calles": _calles_trote(ctx, corredor), "llegada": _calles_trote(ctx, acerc)}
    return (aristas, med, puntuar(med, req, distancia_m))


def ida_y_vuelta(ctx: GraphContext, casa: int, distancia_m: float, req: Requisitos,
                 w: RoutingWeights, n: int = 24) -> list[tuple[list[int], Medida, float]]:
    """Candidatos de ida y vuelta: hasta un punto a media distancia por el camino
    más limpio, y de vuelta por el mismo. Es lo que hace cualquiera que sale a
    hacer un fondo por la ribera del Mapocho, y en ciudad suele tener muchas
    menos detenciones que un circuito."""
    g = ctx.graph
    mitad = distancia_m / 2
    coste, pred = _arbol(ctx, w, casa, mitad * 6)
    metros = _metros_arbol(ctx, coste, pred, casa)
    cand = np.nonzero((metros >= 0.94 * mitad) & (metros <= 1.06 * mitad))[0]
    rev = g.reverse
    out = []
    for destino in (_por_sector(ctx, casa, cand, coste, n) if len(cand) else []):
        ida = _camino(ctx, w, pred, casa, destino)
        aristas = ida + [int(rev[e]) for e in reversed(ida)]
        med = medir(ctx, aristas, req.radio_agua_m)
        out.append((aristas, med, puntuar(med, req, distancia_m)))
    return out


def con_preferidas(req: Requisitos, preferidas: np.ndarray | None) -> Requisitos:
    if preferidas is None:
        return req
    peso = {TipoSesion.FONDO: 50.0, TipoSesion.REGENERATIVO: 40.0}.get(req.tipo, 20.0)
    return dataclasses.replace(req, preferidas=preferidas, peso_preferida=peso)


def subida_partes(partes: list[dict]) -> float:
    """Desnivel positivo de toda la sesión, sumando las partes en orden."""
    return desnivel([c[2] for p in partes for c in (p.get("coordenadas") or [])])[0]


def aviso_tope(req: Requisitos, subida: float) -> list[str]:
    if req.max_subida_m is None or subida <= req.max_subida_m + 1:
        return []
    return [tr(f"No encontré una ruta que suba menos de {req.max_subida_m:.0f} m desde esta "
               f"salida: ésta sube {subida:.0f} m, la que menos de las que probé.",
               f"I couldn't find a route climbing less than {req.max_subida_m:.0f} m from this "
               f"start: this one climbs {subida:.0f} m, the least of those I tried.")]


def _coords(ctx: GraphContext, aristas: list[int]) -> list[list[float]]:
    g = ctx.graph
    if not aristas:
        return []
    a = np.asarray(aristas, dtype=np.int64)
    n = np.concatenate([[ctx.esrc[a[0]]], g.indices[a]])
    return [[round(float(g.lat[i]), 6), round(float(g.lon[i]), 6),
             round(float(g.elev[i]), 1)] for i in n]


def plan_salida(ctx: GraphContext, casa: int) -> list[float]:
    return [round(float(ctx.graph.lat[casa]), 6), round(float(ctx.graph.lon[casa]), 6)]


def _punto(ctx: GraphContext, aristas: list[int], cum: np.ndarray, pos: float) -> list[float]:
    """[lat, lon] a `pos` metros del inicio del recorrido."""
    g = ctx.graph
    a = np.asarray(aristas, dtype=np.int64)
    n = np.concatenate([[ctx.esrc[a[0]]], g.indices[a]])
    return [round(float(np.interp(pos, cum, g.lat[n])), 6),
            round(float(np.interp(pos, cum, g.lon[n])), 6)]


def _resumen_medida(med: Medida) -> dict:
    return {
        "distancia_m": round(med.distancia_m),
        "semaforos": len(med.semaforos),
        "cruces_mayores": med.cruces_mayores,
        "cruces_menores": med.cruces_menores,
        "detenciones": round(med.detenciones(), 1),
        "subida_m": round(med.subida_m),
        "bajada_m": round(med.bajada_m),
        "pendiente_max_pct": round(med.pendiente_max_pct, 1),
        "parque_pct": round(100 * med.parque),
        "sin_pavimento_pct": round(100 * med.sin_pavimento),
        "repetido_pct": round(100 * med.repetido),
        "agua": [{"a_m": round(p), "tipo": t} for p, t in med.agua],
    }


def _marcas(ctx: GraphContext, aristas: list[int], med: Medida,
            desde: float = 0.0) -> list[dict]:
    out = []
    for p in med.semaforos:
        out.append({"tipo": "semaforo", "punto": _punto(ctx, aristas, med.cum, p),
                    "texto": "Semáforo", "a_m": round(p + desde)})
    for p, c in med.cruces:
        if c >= CRUCE_MENOR:
            out.append({"tipo": "cruce", "punto": _punto(ctx, aristas, med.cum, p),
                        "texto": "Cruce", "a_m": round(p + desde)})
    for p, t in med.agua:
        out.append({"tipo": "agua", "punto": _punto(ctx, aristas, med.cum, p),
                    "texto": "Fuente de agua" if t == "drinking_water" else "Baño",
                    "a_m": round(p + desde)})
    return out


# ── Fondo y regenerativo ────────────────────────────────────────────────────

def planificar_circuito(ctx: GraphContext, sesion: Sesion, lat: float, lon: float,
                        volver: bool = True,
                        meta: tuple[float, float] | None = None,
                        preferidas: np.ndarray | None = None) -> dict:
    """Fondo o regenerativo.

    · `volver` (lo habitual): circuito o ida y vuelta desde la salida.
    · sin volver y con `meta`: de la salida a la meta midiendo lo pedido.
    · sin volver y sin meta: termina donde se cumplen los km."""
    if sesion.tipo not in (TipoSesion.FONDO, TipoSesion.REGENERATIVO):
        raise ValueError("planificar_circuito arma fondos y regenerativos")
    req = con_preferidas(requisitos(sesion), preferidas)
    casa = nodo_salida(ctx, lat, lon)
    D = req.distancia_m
    if volver:
        aristas, med, otros = circuito(ctx, casa, D, req)
        forma = getattr(med, "forma", None) or (
            "ida y vuelta" if med.repetido > 0.9 else "circuito")
    else:
        w = pesos_sesion(ctx, casa, D + 1500, req)
        if meta is not None:
            fin = nodo_salida(ctx, *meta)
            cands = hasta_meta(ctx, casa, fin, D, req, w)
            forma = "hasta la meta"
        else:
            cands = solo_ida(ctx, casa, D, req, w)
            forma = "solo ida"
        if not cands:
            raise SinTramo(tr(f"No encontré un recorrido de {D / 1000:.0f} km de solo ida "
                              "dentro del mapa de esta comuna: prueba volviendo a la salida.",
                              f"I couldn't fit a {D / 1000:.0f} km one-way route inside this "
                              "area's map: try returning to the start."))
        cands.sort(key=lambda c: c[2])
        (aristas, med, _), otros = cands[0], cands[1:]
    tipo = sesion.tipo.value
    if forma == "corredor" and fraccion_preferida(med, req) >= 0.5:
        forma = "favoritos"
    info = _resumen_medida(med) | {"calles": _calles_trote(ctx, aristas), "forma": forma,
                                   "preferidas_pct": round(100 * fraccion_preferida(med, req)),
                                   "corredor": getattr(med, "detalle", None)}
    avisos = []
    if abs(med.distancia_m - D) > 0.05 * D:
        avisos.append(tr(f"El recorrido mide {med.distancia_m / 1000:.1f} km: no encontré uno "
                         "más ajustado a la distancia pedida.",
                         f"The route is {med.distancia_m / 1000:.1f} km: I couldn't find one "
                         "closer to the requested distance."))
    avisos += aviso_tope(req, med.subida_m)
    if sesion.tipo is TipoSesion.FONDO and not med.agua:
        avisos.append(tr("No hay fuentes de agua registradas en OpenStreetMap junto a este "
                         "recorrido: lleva agua.",
                         "There are no water fountains in OpenStreetMap along this route: "
                         "bring water."))
    avisos.append(aviso_srtm())
    plan = base_plan(ctx, sesion, req, casa) | {
        "recorrido": info,
        "partes": [{"tipo": tipo, "distancia_m": round(med.distancia_m),
                    "coordenadas": _coords(ctx, aristas)}],
        "marcas": _marcas(ctx, aristas, med),
        "alternativas": [_resumen_medida(m) | {"puntos": round(p, 1)} for _, m, p in otros[:2]],
        "totales": {"distancia_m": round(med.distancia_m), "subida_m": round(med.subida_m),
                    "max_subida_m": req.max_subida_m},
        "avisos": avisos,
        "volver": volver,
        "llegada": _punto(ctx, aristas, med.cum, med.distancia_m),
    }
    plan["explicacion"] = explicar_continuo(plan)
    return plan


# ── Tempo ───────────────────────────────────────────────────────────────────

def _opcion_corredor(ctx: GraphContext, casa: int, sesion: Sesion, req: Requisitos):
    """El corredor sin detenciones más largo (hasta medio bloque), en ida y vuelta."""
    X = req.distancia_m
    largos = [L for L in (X / 2, X / 3, X / 4, X / 5, X / 6, X / 8, X / 10)
              if L >= CORREDOR_MIN_M]
    for L in largos:
        r = dataclasses.replace(req, largo_tramo_m=round(L))
        try:
            tramos, _ = buscar_tramos(ctx, casa, r, holgura_m=sesion.calentamiento_m,
                                      n=1, solo_limpios=True, solo_cerca=True)
        except SinTramo:
            continue
        return tramos[0], round(X / L)
    return None


def _ventana_circuito(med: Medida, X: float, cal: float, vc: float) -> tuple[float, float]:
    """(inicio del bloque, detenciones) con menos detenciones en el circuito."""
    lo, hi = cal * 0.6, med.distancia_m - X - vc * 0.5
    if hi < lo:
        lo = hi = max(0.0, (med.distancia_m - X) / 2)
    mejor = (lo, med.detenciones(lo, lo + X))
    for p in np.arange(lo, hi + 1, 50.0):
        d = med.detenciones(float(p), float(p) + X)
        if d < mejor[1] - 1e-9:
            mejor = (float(p), d)
    return mejor


def planificar_tempo(ctx: GraphContext, sesion: Sesion, lat: float, lon: float,
                     preferidas: np.ndarray | None = None, volver: bool = True,
                     meta: tuple[float, float] | None = None) -> dict:
    """Tempo: circuito (o, sin volver, recorrido hasta la meta o de solo ida)
    partido en calentamiento, bloque y vuelta a la calma; o corredor limpio en
    ida y vuelta, si tiene menos detenciones."""
    req = con_preferidas(requisitos(sesion), preferidas)
    casa = nodo_salida(ctx, lat, lon)
    X = req.distancia_m
    cal, vc = sesion.calentamiento_m, sesion.vuelta_calma_m
    fin_nodo = None if volver or meta is None else nodo_salida(ctx, *meta)

    # Opción circuito (o, sin volver, de la salida a la meta / de solo ida)
    if volver:
        aristas, med, _ = circuito(ctx, casa, cal + X + vc, req, k=8, afinar=2,
                                   con_ida_y_vuelta=False)
    else:
        D = cal + X + vc
        w0 = pesos_sesion(ctx, casa, D + 1500, req)
        cands = (hasta_meta(ctx, casa, fin_nodo, D, req, w0) if fin_nodo is not None
                 else solo_ida(ctx, casa, D, req, w0))
        if not cands:
            raise SinTramo(tr(f"No encontré un recorrido de {D / 1000:.0f} km de solo ida "
                              "dentro del mapa de esta comuna: prueba volviendo a la salida.",
                              f"I couldn't fit a {D / 1000:.0f} km one-way route inside this "
                              "area's map: try returning to the start."))
        aristas, med, _ = min(cands, key=lambda c: c[2])
    ini, det_circ = _ventana_circuito(med, X, cal, vc)

    # Opción corredor
    corr = _opcion_corredor(ctx, casa, sesion, req)
    det_corr = None
    if corr:
        tramo, vueltas = corr
        det_corr = (vueltas - 1) * DETENCION_VUELTA_U

    usar_corredor = bool(corr) and det_corr <= det_circ
    if usar_corredor and req.max_subida_m is not None:
        # Con tope de desnivel: estimar la subida del corredor (trote de ida y
        # vuelta + la mitad del desnivel del tramo por pasada) y, si se pasa y
        # el circuito no, quedarse con el circuito.
        tramo, vueltas = corr
        wt = pesos_trote(ctx, casa, cal + 6000 + 2 * tramo.largo_m)
        ida0 = _tramo_trote(ctx, wt, casa, tramo.nodo_inicio())
        estimada = 2 * (ida0["subida_m"] if ida0 else 0) + vueltas * tramo.desnivel_m / 2
        if estimada > req.max_subida_m and med.subida_m <= req.max_subida_m:
            usar_corredor = False

    avisos = []
    if usar_corredor:
        tramo, vueltas = corr
        w = pesos_trote(ctx, casa, cal + 6000 + 2 * tramo.largo_m)
        a, b = tramo.nodo_inicio(), tramo.nodo_fin()
        fin_a = vueltas % 2 == 0
        opciones = []
        for x, y in ((a, b), (b, a)):
            ida = _tramo_trote(ctx, w, casa, x)
            vuelta = tramo_final(ctx, w, x if fin_a else y, casa, req, vc, volver, fin_nodo)
            if ida and vuelta:
                opciones.append((ida["distancia_m"] + vuelta["distancia_m"], x, ida, vuelta))
        if not opciones:
            raise SinTramo(tr("El corredor encontrado no se puede alcanzar desde la salida.",
                              "The corridor found can't be reached from the start."))
        _, x, ida, vuelta = min(opciones, key=lambda o: o[0])
        info = _tramo_info(ctx, tramo)
        coords = info["coordenadas"] if x == a else info["coordenadas"][::-1]
        info["coordenadas"] = coords
        partes = [{"tipo": "calentamiento", **ida}]
        for k in range(vueltas):
            partes.append({"tipo": "tempo", "numero": k + 1, "distancia_m": round(tramo.largo_m),
                           "coordenadas": coords if k % 2 == 0 else coords[::-1]})
        partes.append({"tipo": "vuelta_calma", **vuelta})
        bloque = vueltas * tramo.largo_m
        total = ida["distancia_m"] + bloque + vuelta["distancia_m"]
        forma = {"forma": "corredor", "tramo": info, "vueltas": vueltas,
                 "detenciones_bloque": round(det_corr, 1),
                 "alternativa": {"forma": "circuito", "detenciones_bloque": round(det_circ, 1)}}
        marcas = [{"tipo": "extremo", "punto": info["inicio"], "texto": "Extremo A"},
                  {"tipo": "extremo", "punto": info["fin"], "texto": "Extremo B"}]
        calentamiento_m, vuelta_m = ida["distancia_m"], vuelta["distancia_m"]
    else:
        # Partir el circuito en calentamiento, bloque y vuelta.
        cum = med.cum
        i0 = int(np.searchsorted(cum, ini))
        i1 = int(np.searchsorted(cum, ini + X))
        a1, a2, a3 = aristas[:i0], aristas[i0:i1], aristas[i1:]
        mb = medir(ctx, a2, 0)
        partes = [
            {"tipo": "calentamiento", "distancia_m": round(cum[i0]),
             "coordenadas": _coords(ctx, a1), "calles": _calles_trote(ctx, a1)},
            {"tipo": "tempo", "numero": 1, "distancia_m": round(mb.distancia_m),
             "coordenadas": _coords(ctx, a2)},
            {"tipo": "vuelta_calma", "distancia_m": round(med.distancia_m - cum[i1]),
             "coordenadas": _coords(ctx, a3), "calles": _calles_trote(ctx, a3)},
        ]
        bloque, total = mb.distancia_m, med.distancia_m
        info = _resumen_medida(mb) | {"calles": _calles_trote(ctx, a2)}
        forma = {"forma": "circuito", "bloque": info,
                 "detenciones_bloque": round(det_circ, 1),
                 "alternativa": ({"forma": "corredor",
                                  "detenciones_bloque": round(det_corr, 1)}
                                 if det_corr is not None else None)}
        marcas = _marcas(ctx, a2, mb, desde=float(cum[i0]))
        calentamiento_m, vuelta_m = round(cum[i0]), round(med.distancia_m - cum[i1])
        if det_circ >= 2:
            avisos.append(tr(f"El bloque tiene unas {det_circ:.0f} detenciones probables: no "
                             "encontré un corredor limpio cerca. Si paras, no cortes el "
                             "cronómetro del bloque.",
                             f"The block has about {det_circ:.0f} likely stops: I couldn't find "
                             "a clean corridor nearby. If you stop, keep the block's clock "
                             "running."))
    if abs(bloque - X) > 0.06 * X:
        avisos.append(tr(f"El bloque mide {bloque / 1000:.1f} km en vez de {X / 1000:.1f} km.",
                         f"The block is {bloque / 1000:.1f} km instead of {X / 1000:.1f} km."))
    subida = subida_partes(partes)
    avisos += aviso_tope(req, subida)
    avisos.append(aviso_srtm())
    plan = base_plan(ctx, sesion, req, casa) | forma | {
        "partes": partes,
        "marcas": marcas,
        "totales": {"distancia_m": round(total), "calentamiento_m": calentamiento_m,
                    "tempo_m": round(bloque), "vuelta_calma_m": vuelta_m,
                    "subida_m": round(subida), "max_subida_m": req.max_subida_m},
        "avisos": avisos,
        "volver": volver,
        "con_meta": fin_nodo is not None,
        "llegada": next((pt["coordenadas"][-1][:2] for pt in reversed(partes) if pt["coordenadas"]),
                        plan_salida(ctx, casa)),
    }
    plan["explicacion"] = explicar_tempo(plan)
    return plan
