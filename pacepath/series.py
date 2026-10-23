"""Series: el tramo para las repeticiones y el plan completo de la sesión.

    salida ──calentamiento──► A ══rep 1══► B ══rep 2══► A … ──vuelta──► salida

1. Sobre los trazos rectos (`tramos.py`) se evalúan todas las ventanas de la
   longitud de la repetición que empiezan en un nodo. Restricciones duras: sin
   semáforos, sin cruces mayores, plano y recto (ver `sesion.Requisitos`).
2. Las que cumplen se ordenan por puntos: cruces menores, desnivel, curvatura,
   lo lejos que quedan de la salida más allá del calentamiento, parque.
3. Con el mejor tramo se arma la sesión: calentamiento por el camino más
   cómodo del motor de RaceEngine, repeticiones en ida y vuelta (la pendiente
   que quede se compensa), vuelta a la calma a casa.

Si nada cumple, se relajan las restricciones por niveles y se avisa de cuál se
relajó: una ruta con un semáforo dicho es mejor que ninguna.
"""
from __future__ import annotations

import contextlib
import dataclasses
import math
from contextvars import ContextVar
from dataclasses import dataclass

import numpy as np

from raceengine_contracts import ConstraintModel, GeoPoint
from raceengine_contracts.constraints import (
    ElevationLayer, GeometryLayer, IntersectionLayer,
)
from raceengine_engine.context import GraphContext
from raceengine_engine.weights import build_weights
from raceengine_geodata import ALL_CLASSES
from raceengine_routing import RoutingWeights

from .idioma import tr
from . import nombres as _nombres
from .sesion import CRUCE_MAYOR, CRUCE_MENOR, Requisitos, Sesion, TipoSesion, requisitos
from .explicacion import explicar_cuestas, explicar_series
from .metricas import desnivel
from .parques import en_parque
from .tramos import nodos_en_red
from .tramos import CLASES_SENDA, Trazo, aristas_corribles, trazos

MARGEN_EXTREMO_M = 8.0        # un cruce justo en el inicio o el fin no corta la serie
LEJANIA_EXTRA_M = 3000.0      # más allá del calentamiento, cuánto se busca primero
# Ignacio prefiere ir más lejos antes que aceptar un cruce: si no hay tramo
# limpio cerca, se busca hasta aquí antes de relajar nada.
LEJANIA_EXTRA_MAX_M = 6000.0
SEPARACION_ALTERNATIVAS_M = 300.0


class SinTramo(Exception):
    """No hay ningún tramo utilizable cerca de la salida."""


# ── Pesos para trotar (calentamiento y vuelta) ──────────────────────────────

# ── Radio máximo: nada de la ruta sale del círculo alrededor de la salida ────
# Lo fija `planificar` para todo el pedido (calles permitidas en los pesos y
# tramos de series dentro del círculo), así no hay que pasarlo por cada función.
_RADIO: ContextVar[tuple[float, float, float] | None] = ContextVar("radio_max", default=None)


@contextlib.contextmanager
def radio_max(ctx: GraphContext, casa: int, radio_m: float | None):
    g = ctx.graph
    tok = _RADIO.set((float(g.x[casa]), float(g.y[casa]), float(radio_m)) if radio_m else None)
    try:
        yield
    finally:
        _RADIO.reset(tok)


def radio_actual() -> float | None:
    r = _RADIO.get()
    return r[2] if r else None


def dentro_radio(ctx: GraphContext, nodos) -> np.ndarray | bool:
    """¿Están los nodos dentro del radio máximo? (True si no hay radio)."""
    r = _RADIO.get()
    if r is None:
        return True
    g = ctx.graph
    cx, cy, rad = r
    return (g.x[nodos] - cx) ** 2 + (g.y[nodos] - cy) ** 2 <= rad * rad


def pesos_trote(ctx: GraphContext, inicio: int, radio_m: float) -> RoutingWeights:
    """Pesos del motor de RaceEngine para el trote de ida y vuelta: llano, con
    pocos semáforos. Se le añaden las sendas de parque, que la disciplina
    «running» del motor no incluye y donde suelen estar los mejores tramos."""
    g = ctx.graph
    lat, lon = float(g.lat[inicio]), float(g.lon[inicio])
    modelo = ConstraintModel(
        region=ctx.region_id,
        start=GeoPoint(lat=lat, lon=lon),
        elevation=ElevationLayer(prefer_flatter=True),
        intersections=IntersectionLayer(penalize_crossings=True),
        geometry=GeometryLayer(search_radius_max_m=min(max(radio_m, 500.0), 150_000)),
    )
    w, _ = build_weights(g, modelo, ctx.esrc, inicio)
    sendas = aristas_corribles(ctx) & np.isin(g.eclass, CLASES_SENDA)
    dx = g.x[ctx.esrc] - g.x[inicio]
    dy = g.y[ctx.esrc] - g.y[inicio]
    sendas &= dx * dx + dy * dy <= radio_m * radio_m
    allowed = w.allowed_edges | sendas
    if _RADIO.get() is not None:
        allowed &= dentro_radio(ctx, ctx.esrc) & dentro_radio(ctx, g.indices)
    weight = w.weight.copy()
    weight[sendas] = g.elen[sendas] * np.float32(0.9)
    return RoutingWeights(allowed_edges=allowed, weight=weight,
                          min_ratio=min(w.min_ratio, 0.9), max_ratio=w.max_ratio)


# ── Evaluación de ventanas ──────────────────────────────────────────────────

@dataclass
class Tramo:
    trazo: Trazo
    i: int                     # índice del nodo de inicio en el trazo
    largo_m: float
    semaforos: int
    cruces_mayores: int
    cruces_menores: int
    coste_cruces: float
    desnivel_m: float          # máx − mín dentro del tramo
    rectitud: float
    parque: float              # fracción del largo por parque
    sin_pavimento: float
    acercamiento_m: float      # por calle desde la salida hasta el extremo más cercano
    puntos: float = 0.0
    pendiente_pct: float = 0.0  # media, con signo, en el sentido del trazo

    # Geometría y perfil exactos (el final se interpola a mitad de arista).
    def perfil(self, ctx: GraphContext, paso_m: float = 10.0):
        """(dist, x, y, elev) muestreados cada `paso_m` a lo largo del tramo."""
        g, t = ctx.graph, self.trazo
        c0 = t.cum[self.i]
        d = np.append(np.arange(0.0, self.largo_m, paso_m), self.largo_m)
        cum = t.cum - c0
        n = t.nodos
        return (d, np.interp(d, cum, g.x[n]), np.interp(d, cum, g.y[n]),
                np.interp(d, cum, g.elev[n].astype(np.float64)))

    def coordenadas(self, ctx: GraphContext) -> list[tuple[float, float, float]]:
        """[(lat, lon, elev)] del tramo: nodos del grafo y extremo interpolado."""
        g, t = ctx.graph, self.trazo
        c0 = t.cum[self.i]
        fin = c0 + self.largo_m
        j = int(np.searchsorted(t.cum, fin))
        pts = [(float(g.lat[n]), float(g.lon[n]), float(g.elev[n]))
               for n in t.nodos[self.i:j]]
        if j < len(t.nodos):
            a, b = t.nodos[j - 1], t.nodos[j]
            f = (fin - t.cum[j - 1]) / max(t.cum[j] - t.cum[j - 1], 1e-9)
            pts.append((float(g.lat[a] + f * (g.lat[b] - g.lat[a])),
                        float(g.lon[a] + f * (g.lon[b] - g.lon[a])),
                        float(g.elev[a] + f * (g.elev[b] - g.elev[a]))))
        return pts

    def nodo_inicio(self) -> int:
        return int(self.trazo.nodos[self.i])

    def nodo_fin(self) -> int:
        """Nodo del grafo más cercano al final exacto de la repetición."""
        t = self.trazo
        fin = t.cum[self.i] + self.largo_m
        j = min(int(np.searchsorted(t.cum, fin)), len(t.nodos) - 1)
        if j > self.i + 1 and fin - t.cum[j - 1] < t.cum[j] - fin:
            j -= 1
        return int(t.nodos[j])

    def cruces(self, ctx: GraphContext) -> list[dict]:
        g, t = ctx.graph, self.trazo
        c0 = t.cum[self.i]
        out = []
        for k in range(self.i + 1, len(t.nodos)):
            d = t.cum[k] - c0
            if d >= self.largo_m - MARGEN_EXTREMO_M:
                break
            if d > MARGEN_EXTREMO_M and (t.cruce[k] > 0 or t.semaforo[k]):
                n = t.nodos[k]
                out.append({"lat": float(g.lat[n]), "lon": float(g.lon[n]),
                            "a_m": round(float(d)), "coste": float(t.cruce[k]),
                            "semaforo": bool(t.semaforo[k])})
        return out


def _ventanas(ctx: GraphContext, t: Trazo, req: Requisitos, dist_casa: np.ndarray,
              holgura_m: float, max_acerc_m: float = math.inf) -> Tramo | None:
    """La mejor ventana de largo `req.largo_tramo_m` dentro del trazo."""
    L = req.largo_tramo_m
    if t.largo_m < L:
        return None
    g = ctx.graph
    cum, nodos = t.cum, t.nodos
    inicios = np.nonzero(cum + L <= cum[-1] + 1e-6)[0]
    acerc = dist_casa[nodos]
    if not np.isfinite(acerc).any():
        return None

    # Prefijos para contar en O(1) por ventana.
    sem = np.concatenate([[0], np.cumsum(t.semaforo)])
    may = np.concatenate([[0], np.cumsum(t.cruce >= CRUCE_MAYOR)])
    men = np.concatenate([[0], np.cumsum((t.cruce >= CRUCE_MENOR) & (t.cruce < CRUCE_MAYOR))])
    # Coste blando: sólo lo que queda por debajo de un cruce de calle (servicio).
    cmen = np.concatenate([[0.0], np.cumsum(np.where(t.cruce < CRUCE_MENOR, t.cruce, 0))])
    largo_e = g.elen[t.aristas].astype(np.float64)
    parque = np.concatenate([[0.0], np.cumsum(largo_e * en_parque(ctx)[t.aristas])])
    tierra = np.concatenate([[0.0], np.cumsum(largo_e * ~g.epaved[t.aristas])])

    # Nodos interiores de la ventana [cum_i + margen, cum_i + L − margen).
    a = np.searchsorted(cum, cum[inicios] + MARGEN_EXTREMO_M, side="right")
    b = np.searchsorted(cum, cum[inicios] + L - MARGEN_EXTREMO_M, side="left")
    n_sem = sem[b] - sem[a]
    n_may = may[b] - may[a]
    n_men = men[b] - men[a]
    ok = ((n_sem <= req.max_semaforos) & (n_may <= req.max_cruces_mayores)
          & (n_men <= req.max_cruces_menores))
    if not ok.any():
        return None

    fin = cum[inicios] + L
    j = np.searchsorted(cum, fin)                    # primer nodo en o tras el final
    j = np.minimum(j, len(cum) - 1)
    xf = np.interp(fin, cum, g.x[nodos])
    yf = np.interp(fin, cum, g.y[nodos])
    rect = np.hypot(xf - g.x[nodos[inicios]], yf - g.y[nodos[inicios]]) / L
    ok &= rect >= req.min_rectitud
    acerc_v = np.minimum(acerc[inicios], acerc[j])
    ok &= np.isfinite(acerc_v) & (acerc_v <= max_acerc_m)
    if not ok.any():
        return None

    elev = g.elev[nodos].astype(np.float64)
    zf = np.interp(fin, cum, elev)
    pend = (zf - elev[inicios]) / L * 100.0
    objetivo = req.pendiente_objetivo_pct
    if objetivo is not None:
        # Cuestas: vale en cualquiera de los dos sentidos (se sube por donde sube).
        ok &= np.abs(np.abs(pend) - objetivo) <= req.tolerancia_pendiente_pct
    mejor: Tramo | None = None
    for k in np.nonzero(ok)[0]:
        i = int(inicios[k])
        tramo_e = np.append(elev[i:int(j[k])], zf[k])
        desn = float(tramo_e.max() - tramo_e.min())
        if desn > req.max_desnivel_tramo_m:
            continue
        if objetivo is not None and desn > abs(zf[k] - elev[i]) * 1.25 + 3.0:
            continue        # sube y baja: no es una cuesta, es un serrucho
        # Fracciones por parque/tierra, aproximadas a las aristas completas.
        p = (parque[j[k]] - parque[i]) / max(cum[j[k]] - cum[i], 1.0)
        s = (tierra[j[k]] - tierra[i]) / max(cum[j[k]] - cum[i], 1.0)
        coste = float(cmen[b[k]] - cmen[a[k]])
        lejos = max(0.0, float(acerc_v[k]) - holgura_m)
        puntos = (req.peso_cruce * coste
                  + req.peso_desnivel_m * desn
                  + (req.peso_pendiente * abs(abs(float(pend[k])) - objetivo)
                     if objetivo is not None else 0.0)
                  + req.peso_curvatura * (1.0 - float(rect[k]))
                  + req.peso_lejania_100m * lejos / 100.0
                  + req.peso_sin_pavimento * s
                  - req.bonus_parque * p
                  # Penalización fuerte, no descarte: sólo existe en los niveles
                  # relajados, y ahí un semáforo debe pesar más que todo lo demás.
                  + 200.0 * float(n_sem[k]) + 120.0 * float(n_may[k])
                  + 60.0 * float(n_men[k]))
        if mejor is None or puntos < mejor.puntos:
            mejor = Tramo(
                trazo=t, i=i, largo_m=L, semaforos=int(n_sem[k]),
                cruces_mayores=int(n_may[k]), cruces_menores=int(n_men[k]),
                coste_cruces=coste, desnivel_m=desn, rectitud=float(rect[k]),
                parque=float(p), sin_pavimento=float(s),
                acercamiento_m=float(acerc_v[k]), puntos=puntos,
                pendiente_pct=float(pend[k]),
            )
    return mejor


def _niveles(req: Requisitos, holgura_m: float) -> list[tuple[Requisitos, float, str | None]]:
    """(requisitos, distancia máxima de acercamiento, aviso) de más a menos estricto.

    Primero se va más lejos; sólo después se acepta un cruce, y lo último que se
    acepta es un semáforo."""
    r = dataclasses.replace
    cerca = holgura_m + LEJANIA_EXTRA_M
    lejos = holgura_m + LEJANIA_EXTRA_MAX_M
    return [
        (req, cerca, None),
        (req, lejos, tr("No hay un tramo sin cruces más cerca: éste es el más próximo.",
                        "There's no crossing-free stretch closer: this is the nearest one.")),
        (r(req, max_cruces_menores=2), lejos,
         tr(f"No hay ningún tramo sin cruces a menos de {lejos / 1000:.0f} km: éste tiene "
            "como mucho dos cruces de calle residencial.",
            f"There's no crossing-free stretch within {lejos / 1000:.0f} km: this one has at "
            "most two residential street crossings.")),
        (r(req, max_cruces_menores=4, max_cruces_mayores=1, max_semaforos=1,
           max_desnivel_tramo_m=req.max_desnivel_tramo_m * 1.5,
           min_rectitud=req.min_rectitud - 0.1), lejos,
         tr("No hay un tramo limpio cerca: éste tiene algún cruce y como mucho un "
            "semáforo. Si te toca en rojo, alarga esa pausa.",
            "There's no clean stretch nearby: this one has some crossings and at most one "
            "traffic light. If it's red, make that rest longer.")),
    ]


def buscar_tramos(ctx: GraphContext, inicio: int, req: Requisitos, holgura_m: float,
                  n: int = 3, solo_limpios: bool = False,
                  solo_cerca: bool = False) -> tuple[list[Tramo], str | None]:
    """Los `n` mejores tramos para la repetición, separados entre sí.

    Con `solo_limpios` no se relajan semáforos ni cruces (sí la distancia)."""
    radio = holgura_m + LEJANIA_EXTRA_MAX_M
    if radio_actual() is not None:
        radio = min(radio, radio_actual())
    w = pesos_trote(ctx, inicio, radio + req.largo_tramo_m)
    dist_casa = ctx.provider.reachable(inicio, w, radio)
    g = ctx.graph
    sx, sy = float(g.x[inicio]), float(g.y[inicio])
    cerca = []
    for t in trazos(ctx, req.sendas_de_parque):
        if t.largo_m < req.largo_tramo_m:
            continue
        # Descarte barato por distancia en línea recta antes de mirar ventanas.
        d2 = (g.x[t.nodos] - sx) ** 2 + (g.y[t.nodos] - sy) ** 2
        if d2.min() <= radio * radio:
            cerca.append(t)

    niveles = _niveles(req, holgura_m)
    if solo_cerca:
        niveles = niveles[:1]
    elif solo_limpios:
        niveles = niveles[:2]
    for nivel, max_acerc, aviso in niveles:
        cand = [m for t in cerca
                if (m := _ventanas(ctx, t, nivel, dist_casa, holgura_m, max_acerc))]
        if radio_actual() is not None:
            # El tramo es recto: si sus dos extremos caen en el círculo, cae entero.
            cand = [m for m in cand if np.all(dentro_radio(ctx, [m.nodo_inicio(), m.nodo_fin()]))]
        if not cand:
            continue
        cand.sort(key=lambda m: m.puntos)
        elegidos: list[Tramo] = []
        for m in cand:
            mx, my = _centro(ctx, m)
            if all(math.hypot(mx - ox, my - oy) >= SEPARACION_ALTERNATIVAS_M
                   for ox, oy in (_centro(ctx, e) for e in elegidos)):
                elegidos.append(m)
            if len(elegidos) == n:
                break
        return elegidos, aviso
    if req.pendiente_objetivo_pct is not None:
        que = tr(f"con {req.pendiente_objetivo_pct:g} % de pendiente",
                 f"at {req.pendiente_objetivo_pct:g} % grade")
    else:
        que = tr("sin semáforos", "without traffic lights")
    raise SinTramo(tr(
        f"No encontré ningún tramo de {req.largo_tramo_m:.0f} m {que} a menos "
        f"de {radio / 1000:.1f} km de la salida.",
        f"I couldn't find any {req.largo_tramo_m:.0f} m stretch {que} within "
        f"{radio / 1000:.1f} km of the start."))


def _centro(ctx: GraphContext, m: Tramo) -> tuple[float, float]:
    _, x, y, _ = m.perfil(ctx, paso_m=max(m.largo_m / 2, 1.0))
    return float(x[len(x) // 2]), float(y[len(y) // 2])


# ── Plan de la sesión ───────────────────────────────────────────────────────

def _tramo_info(ctx: GraphContext, m: Tramo) -> dict:
    g = ctx.graph
    d, _, _, z = m.perfil(ctx, paso_m=10.0)
    subida, _ = desnivel(z)
    # Pendiente máxima sostenida en 50 m (con 10 m el ruido del SRTM manda).
    k = 5
    pend = np.abs(z[k:] - z[:-k]) / np.maximum(d[k:] - d[:-k], 1.0) * 100 if len(z) > k else [0]
    t = m.trazo
    j = int(np.searchsorted(t.cum, t.cum[m.i] + m.largo_m))
    aristas = t.aristas[m.i:j]
    clases = {}
    for e in aristas:
        c = ALL_CLASSES[g.eclass[e]]
        clases[c] = clases.get(c, 0.0) + float(g.elen[e])
    coords = m.coordenadas(ctx)
    cruces = m.cruces(ctx)
    tabla = _nombres.tabla(ctx)
    calles: list[str] = []
    if tabla is not None:
        calles = tabla.principal(ctx, aristas)
        for c in cruces:
            nodo = int(g.nearest_node(c["lat"], c["lon"]))
            c["calle"] = tabla.del_nodo(ctx, nodo, set(calles))
    return {
        "calles": calles,
        "inicio": [round(coords[0][0], 6), round(coords[0][1], 6)],
        "fin": [round(coords[-1][0], 6), round(coords[-1][1], 6)],
        "largo_m": round(m.largo_m),
        "semaforos": m.semaforos,
        "cruces_mayores": m.cruces_mayores,
        "cruces_menores": m.cruces_menores,
        "cruces": cruces,
        "desnivel_m": round(m.desnivel_m, 1),
        "subida_m": round(subida, 1),
        "pendiente_media_pct": round(abs(z[-1] - z[0]) / m.largo_m * 100, 2),
        "pendiente_max_pct": round(float(np.max(pend)), 1),
        "rectitud": round(m.rectitud, 3),
        "parque_pct": round(100 * m.parque),
        "sin_pavimento_pct": round(100 * m.sin_pavimento),
        "vias": {c: round(v) for c, v in sorted(clases.items(), key=lambda x: -x[1])},
        "acercamiento_m": round(m.acercamiento_m),
        "puntos": round(m.puntos, 1),
        "coordenadas": [[round(a, 6), round(b, 6), round(c, 1)] for a, b, c in coords],
    }


def _calles_trote(ctx: GraphContext, aristas: list[int]) -> list[str]:
    """Calles del trote en orden de paso, sin repetir las consecutivas."""
    tabla = _nombres.tabla(ctx)
    if tabla is None or not aristas:
        return []
    out: list[str] = []
    for n in tabla.de_aristas(ctx, aristas):
        if n and (not out or out[-1] != n) and n not in out:
            out.append(n)
    return out


def _tramo_trote(ctx: GraphContext, w: RoutingWeights, a: int, b: int) -> dict | None:
    if a == b:
        return {"distancia_m": 0, "semaforos": 0, "calles": [], "subida_m": 0.0,
                "coordenadas": []}
    leg = ctx.provider.shortest_path(a, b, w)
    if leg is None or not leg.ok:
        return None
    g = ctx.graph
    n = np.asarray(leg.nodes)
    z = g.elev[n].astype(np.float64)
    return {
        "distancia_m": round(leg.distance_m),
        "semaforos": int(g.is_signal[n].sum()),
        "calles": _calles_trote(ctx, leg.edges),
        "subida_m": round(desnivel(z)[0], 1),
        "coordenadas": [[round(float(g.lat[i]), 6), round(float(g.lon[i]), 6),
                         round(float(g.elev[i]), 1)] for i in n],
    }


def _trote_de_aristas(ctx: GraphContext, aristas: list[int]) -> dict:
    g = ctx.graph
    n = np.concatenate([[ctx.esrc[aristas[0]]], g.indices[aristas]])
    z = g.elev[n].astype(np.float64)
    return {
        "distancia_m": round(float(g.elen[aristas].sum())),
        "semaforos": int(g.is_signal[n].sum()),
        "calles": _calles_trote(ctx, aristas),
        "subida_m": round(desnivel(z)[0], 1),
        "coordenadas": [[round(float(g.lat[i]), 6), round(float(g.lon[i]), 6),
                         round(float(g.elev[i]), 1)] for i in n],
    }


def tramo_final(ctx: GraphContext, w: RoutingWeights, desde: int, casa: int,
                req: Requisitos, vuelta_calma_m: float, volver: bool = True,
                meta: int | None = None) -> dict | None:
    """La vuelta a la calma desde donde termina el bloque: a la salida (lo
    habitual), a la meta, o —sin volver ni meta— un trote suave de lo pedido
    por el camino más limpio, que termina donde se cumple."""
    if volver:
        return _tramo_trote(ctx, w, desde, casa)
    if meta is not None:
        return _tramo_trote(ctx, w, desde, meta)
    if vuelta_calma_m >= 100:
        from .continuo import solo_ida     # continuo importa de aquí
        cands = solo_ida(ctx, desde, vuelta_calma_m, req, w)
        if cands:
            return _trote_de_aristas(ctx, min(cands, key=lambda c: c[2])[0])
    return _tramo_trote(ctx, w, desde, desde)


def nodo_salida(ctx: GraphContext, lat: float, lon: float) -> int:
    """Nodo de la red corrible principal más cercano al punto de salida."""
    return int(ctx.graph.nearest_node(lat, lon, mask=nodos_en_red(ctx)))


def recortar(coords: list[list[float]], metros: float) -> list[list[float]]:
    """Los primeros `metros` de una polilínea [[lat, lon, (elev)], …] (el último
    punto, interpolado)."""
    if len(coords) < 2:
        return coords
    out, hecho = [coords[0]], 0.0
    for a, b in zip(coords, coords[1:]):
        k = math.cos(math.radians((a[0] + b[0]) / 2))
        d = math.hypot((b[1] - a[1]) * k, b[0] - a[0]) * 111_195.0
        if hecho + d >= metros:
            f = (metros - hecho) / d if d else 0.0
            out.append([round(x + (y - x) * f, 6) for x, y in zip(a, b)])
            return out
        out.append(b)
        hecho += d
    return out


def base_plan(ctx: GraphContext, sesion: Sesion, req: Requisitos, casa: int) -> dict:
    g = ctx.graph
    return {
        "sesion": sesion.model_dump(mode="json") | {"resumen": sesion.resumen},
        "comuna": ctx.region_id,
        "salida": [round(float(g.lat[casa]), 6), round(float(g.lon[casa]), 6)],
        "requisitos": {k: v for k, v in dataclasses.asdict(req).items()
                       if k not in ("tipo", "notas", "preferidas")},
        "notas": list(req.notas),
    }


def aviso_srtm() -> str:
    return tr("Elevación del modelo SRTM (±2–3 m): el desnivel es orientativo.",
              "Elevation from the SRTM model (±2–3 m): climb figures are approximate.")


def planificar_series(ctx: GraphContext, sesion: Sesion, lat: float, lon: float,
                      alternativas: int = 3) -> dict:
    """Series y cuestas: un tramo que se repite.

    Series: ida y vuelta (la 1.ª de ida, la 2.ª de vuelta…).
    Cuestas: siempre de subida; la bajada trotando es la recuperación.
    La sesión son las repeticiones: el trote hasta el tramo se muestra para
    saber cómo llegar (sirve de calentamiento) y después no se dibuja nada —
    cada uno vuelve como quiera."""
    if sesion.tipo not in (TipoSesion.SERIES, TipoSesion.CUESTAS):
        raise ValueError("planificar_series arma series y cuestas")
    cuestas = sesion.tipo is TipoSesion.CUESTAS
    req = requisitos(sesion)
    casa = nodo_salida(ctx, lat, lon)

    tramos, aviso = buscar_tramos(ctx, casa, req, holgura_m=sesion.calentamiento_m,
                                  n=alternativas)
    m = tramos[0]
    w = pesos_trote(ctx, casa, sesion.calentamiento_m + LEJANIA_EXTRA_MAX_M
                    + 2 * m.largo_m)
    a, b = m.nodo_inicio(), m.nodo_fin()
    n = int(sesion.repeticiones)

    if cuestas:
        # Se empieza y se termina abajo.
        abajo = a if m.pendiente_pct >= 0 else b
        candidatos = [(abajo, abajo)]
    else:
        # Empezar por el extremo más cercano.
        candidatos = [(a, None), (b, None)]
    opciones = []
    for ini, _ in candidatos:
        ida = _tramo_trote(ctx, w, casa, ini)
        if ida:
            opciones.append((ida["distancia_m"], ini, ida))
    if not opciones:
        raise SinTramo(tr("El tramo encontrado no se puede alcanzar desde la salida.",
                          "The stretch found can't be reached from the start."))
    _, ini, ida = min(opciones, key=lambda o: o[0])

    info = _tramo_info(ctx, m)
    coords = info["coordenadas"]
    if ini != a:
        coords = coords[::-1]
        info["inicio"], info["fin"] = info["fin"], info["inicio"]
    info["coordenadas"] = coords
    info["pendiente_pct"] = round(m.pendiente_pct * (1 if ini == a else -1), 1)

    # Pirámide: todas salen del inicio del tramo y corren su largo; la vuelta
    # al inicio, trotando, es la recuperación (como en las cuestas).
    largos = [float(x) for x in sesion.escalera_m] if sesion.escalera_m else [m.largo_m] * n
    partes = [{"tipo": "calentamiento", **ida}]
    for k in range(n):
        if sesion.escalera_m:
            tramo_k = recortar(coords, largos[k])
            partes.append({"tipo": "serie", "numero": k + 1, "distancia_m": round(largos[k]),
                           "pausa_s": sesion.pausa_s or 0, "coordenadas": tramo_k})
            if k < n - 1:
                partes.append({"tipo": "recuperacion", "numero": k + 1, "distancia_m": round(largos[k]),
                               "coordenadas": tramo_k[::-1]})
        elif cuestas:
            partes.append({"tipo": "serie", "numero": k + 1, "distancia_m": round(m.largo_m),
                           "pausa_s": 0, "coordenadas": coords})
            partes.append({"tipo": "recuperacion", "numero": k + 1,
                           "distancia_m": round(m.largo_m), "coordenadas": coords[::-1]})
        else:
            partes.append({
                "tipo": "serie", "numero": k + 1, "distancia_m": round(m.largo_m),
                "pausa_s": (sesion.pausa_s or 0) if k < n - 1 else 0,
                "coordenadas": coords if k % 2 == 0 else coords[::-1],
            })

    recuperacion = n * m.largo_m if cuestas else sum(largos[:-1]) if sesion.escalera_m else 0.0
    calentamiento_extra = max(0, round(sesion.calentamiento_m - ida["distancia_m"]))
    total = ida["distancia_m"] + sum(largos) + recuperacion + calentamiento_extra
    avisos = [aviso] if aviso else []
    if calentamiento_extra >= 200:
        avisos.append(tr(f"El tramo está a {ida['distancia_m']} m: completa el calentamiento "
                         f"con {calentamiento_extra} m suaves por el mismo tramo.",
                         f"The stretch is {ida['distancia_m']} m away: finish your warm-up "
                         f"with {calentamiento_extra} m easy on the same stretch."))
    avisos.append(aviso_srtm())

    plan = base_plan(ctx, sesion, req, casa) | {
        "tramo": info,
        "alternativas": [_tramo_info(ctx, t) for t in tramos[1:]],
        "partes": partes,
        "totales": {
            "distancia_m": round(total),
            "calentamiento_m": ida["distancia_m"] + calentamiento_extra,
            "series_m": round(sum(largos)),
            "recuperacion_m": round(recuperacion),
            "semaforos_trote": ida["semaforos"],
        },
        "avisos": avisos,
    }
    plan["explicacion"] = explicar_cuestas(plan) if cuestas else explicar_series(plan)
    return plan
