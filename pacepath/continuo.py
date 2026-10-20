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
from raceengine_engine.generate import generate_loops
from raceengine_engine.repair import adjust_distance
from raceengine_routing import RoutingWeights

from .explicacion import explicar_continuo, explicar_tempo
from .metricas import Medida, medir
from .sesion import COSTE_CRUCE, CRUCE_MENOR, Requisitos, Sesion, TipoSesion, requisitos
from .series import (
    AVISO_SRTM, SinTramo, _calles_trote, _tramo_info, _tramo_trote, base_plan,
    buscar_tramos, nodo_salida, pesos_trote,
)
from .parques import en_parque
from .tramos import semaforos_cercanos

_COSTE = np.array([COSTE_CRUCE.get(c, 0.0) for c in ALL_CLASSES], dtype=np.float32)

# Una vuelta en U en el corredor cuesta como un cruce residencial: frenar,
# girar, volver a coger el ritmo.
DETENCION_VUELTA_U = 0.2
CORREDOR_MIN_M = 800.0


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
    mult[en_parque(ctx)] *= np.float32(req.mult_parque)
    mult[~g.epaved] *= np.float32(req.mult_sin_pavimento)
    if req.mult_subida:
        mult *= (1.0 + req.mult_subida * np.maximum(g.egrad, 0)).astype(np.float32)

    weight = (base.weight * mult + extra).astype(np.float32)
    ok = base.allowed_edges & (g.elen > 0)
    ratio = weight[ok] / g.elen[ok]
    return RoutingWeights(allowed_edges=base.allowed_edges, weight=weight,
                          min_ratio=max(float(ratio.min()), 0.05),
                          max_ratio=max(float(ratio.max()), 1.0))


# ── Circuitos ───────────────────────────────────────────────────────────────

def puntuar(med: Medida, req: Requisitos, objetivo_m: float) -> float:
    """Puntos de penalización por km (menos es mejor)."""
    km = max(med.distancia_m / 1000, 0.1)
    p = req.peso_detencion * med.detenciones() / km
    p += req.peso_subida * med.subida_m / km
    p -= 40.0 * (1.0 - req.mult_parque) * med.parque
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
             k: int = 8, afinar: int = 2,
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
                               search_radius_max_m=distancia_m * 0.6 + 1500),
    )
    reach = ctx.provider.reachable(casa, w, distancia_m * 0.6)
    rng = _semilla(casa, distancia_m, req.tipo.value)
    loops = generate_loops(ctx, modelo, w, casa, reach, rng, k=k, budget_ms=2000)
    if not loops:
        raise SinTramo("No pude cerrar un circuito desde esa salida.")

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
    todos.sort(key=lambda x: x[2])
    return todos[0][0], todos[0][1], todos[1:]


def ida_y_vuelta(ctx: GraphContext, casa: int, distancia_m: float, req: Requisitos,
                 w: RoutingWeights, n: int = 16) -> list[tuple[list[int], Medida, float]]:
    """Candidatos de ida y vuelta: hasta un punto a media distancia por el camino
    más limpio, y de vuelta por el mismo. Es lo que hace cualquiera que sale a
    hacer un fondo por la ribera del Mapocho, y en ciudad suele tener muchas
    menos detenciones que un circuito."""
    g = ctx.graph
    mitad = distancia_m / 2
    dist = ctx.provider.reachable(casa, w, mitad)
    cand = np.nonzero((dist >= 0.55 * mitad) & (dist <= 0.95 * mitad) & (g.degree >= 3))[0]
    if len(cand) == 0:
        return []
    # Repartidos por rumbo: uno por sector, el más lejano del sector.
    ang = np.arctan2(g.y[cand] - g.y[casa], g.x[cand] - g.x[casa])
    sector = ((ang + np.pi) / (2 * np.pi) * n).astype(int) % n
    elegidos = []
    for k in range(n):
        en = cand[sector == k]
        if len(en):
            elegidos.append(int(en[np.argmax(dist[en])]))
    rev = g.reverse
    out = []
    for destino in elegidos:
        leg = ctx.provider.shortest_path(casa, destino, w)
        if leg is None or not leg.ok:
            continue
        if not 0.85 * mitad <= leg.distance_m <= 1.10 * mitad:
            continue
        aristas = list(leg.edges) + [int(rev[e]) for e in reversed(leg.edges)]
        med = medir(ctx, aristas, req.radio_agua_m)
        out.append((aristas, med, puntuar(med, req, distancia_m)))
    return out


def _coords(ctx: GraphContext, aristas: list[int]) -> list[list[float]]:
    g = ctx.graph
    if not aristas:
        return []
    a = np.asarray(aristas, dtype=np.int64)
    n = np.concatenate([[ctx.esrc[a[0]]], g.indices[a]])
    return [[round(float(g.lat[i]), 6), round(float(g.lon[i]), 6),
             round(float(g.elev[i]), 1)] for i in n]


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

def planificar_circuito(ctx: GraphContext, sesion: Sesion, lat: float, lon: float) -> dict:
    if sesion.tipo not in (TipoSesion.FONDO, TipoSesion.REGENERATIVO):
        raise ValueError("planificar_circuito arma fondos y regenerativos")
    req = requisitos(sesion)
    casa = nodo_salida(ctx, lat, lon)
    aristas, med, otros = circuito(ctx, casa, req.distancia_m, req)
    tipo = sesion.tipo.value
    forma = "ida y vuelta" if med.repetido > 0.9 else "circuito"
    info = _resumen_medida(med) | {"calles": _calles_trote(ctx, aristas), "forma": forma}
    avisos = []
    if abs(med.distancia_m - req.distancia_m) > 0.05 * req.distancia_m:
        avisos.append(f"El circuito mide {med.distancia_m / 1000:.1f} km: no encontré uno "
                      "más ajustado a la distancia pedida desde esta salida.")
    if sesion.tipo is TipoSesion.FONDO and not med.agua:
        avisos.append("No hay fuentes de agua registradas en OpenStreetMap junto a este "
                      "recorrido: lleva agua.")
    avisos.append(AVISO_SRTM)
    plan = base_plan(ctx, sesion, req, casa) | {
        "recorrido": info,
        "partes": [{"tipo": tipo, "distancia_m": round(med.distancia_m),
                    "coordenadas": _coords(ctx, aristas)}],
        "marcas": _marcas(ctx, aristas, med),
        "alternativas": [_resumen_medida(m) | {"puntos": round(p, 1)} for _, m, p in otros[:2]],
        "totales": {"distancia_m": round(med.distancia_m)},
        "avisos": avisos,
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


def planificar_tempo(ctx: GraphContext, sesion: Sesion, lat: float, lon: float) -> dict:
    req = requisitos(sesion)
    casa = nodo_salida(ctx, lat, lon)
    X = req.distancia_m
    cal, vc = sesion.calentamiento_m, sesion.vuelta_calma_m

    # Opción circuito
    aristas, med, _ = circuito(ctx, casa, cal + X + vc, req, k=8, afinar=2,
                               con_ida_y_vuelta=False)
    ini, det_circ = _ventana_circuito(med, X, cal, vc)

    # Opción corredor
    corr = _opcion_corredor(ctx, casa, sesion, req)
    det_corr = None
    if corr:
        tramo, vueltas = corr
        det_corr = (vueltas - 1) * DETENCION_VUELTA_U

    avisos = []
    if corr and det_corr <= det_circ:
        tramo, vueltas = corr
        w = pesos_trote(ctx, casa, cal + 6000 + 2 * tramo.largo_m)
        a, b = tramo.nodo_inicio(), tramo.nodo_fin()
        fin_a = vueltas % 2 == 0
        opciones = []
        for x, y in ((a, b), (b, a)):
            ida = _tramo_trote(ctx, w, casa, x)
            vuelta = _tramo_trote(ctx, w, x if fin_a else y, casa)
            if ida and vuelta:
                opciones.append((ida["distancia_m"] + vuelta["distancia_m"], x, ida, vuelta))
        if not opciones:
            raise SinTramo("El corredor encontrado no se puede alcanzar desde la salida.")
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
            avisos.append(f"El bloque tiene unas {det_circ:.0f} detenciones probables: no "
                          "encontré un corredor limpio cerca. Si paras, no cortes el "
                          "cronómetro del bloque.")
    if abs(bloque - X) > 0.06 * X:
        avisos.append(f"El bloque mide {bloque / 1000:.1f} km en vez de {X / 1000:.1f} km.")
    avisos.append(AVISO_SRTM)
    plan = base_plan(ctx, sesion, req, casa) | forma | {
        "partes": partes,
        "marcas": marcas,
        "totales": {"distancia_m": round(total), "calentamiento_m": calentamiento_m,
                    "tempo_m": round(bloque), "vuelta_calma_m": vuelta_m},
        "avisos": avisos,
    }
    plan["explicacion"] = explicar_tempo(plan)
    return plan
