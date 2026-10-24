"""Nombres de calle por arista, desde la Release «nombres» de PacePath.

El grafo de RaceEngine guarda el id OSM de cada nodo pero no el nombre de las
vías. `scripts/ci_nombres.py` publica, por comuna, las vías con nombre y su lista
de nodos OSM; aquí cada par de nodos consecutivos se traduce a índices del grafo
y queda una tabla ordenada (u·N + v → nombre) que se consulta con searchsorted.

Si la Release no está (o no hay red), todo sigue funcionando sin nombres: la
explicación usa «el tramo» y las coordenadas.
"""
from __future__ import annotations

import gzip
import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from raceengine_engine.context import GraphContext

NOMBRES_URL = os.environ.get(
    "PACEPATH_NOMBRES_URL",
    "https://github.com/ignacioarteaga99/PacePath/releases/download/nombres",
).rstrip("/")
_DIR = Path(os.environ["RACEENGINE_DATA"]) / "nombres"
_lock = threading.Lock()
REFRESCO_S = 24 * 3600     # el workflow puede haber publicado una versión nueva


@dataclass
class TablaNombres:
    claves: np.ndarray        # int64 ordenado: u·N + v (u < v, índices del grafo)
    nombre: np.ndarray        # int32 índice en `nombres`
    nombres: list[str]
    puntos: list[tuple[str, float, float]]
    parques: list[list[list[float]]]
    n_nodos: int

    def de_aristas(self, ctx: GraphContext, aristas) -> list[str | None]:
        a = np.asarray(aristas, dtype=np.int64)
        u = ctx.esrc[a].astype(np.int64)
        v = ctx.graph.indices[a].astype(np.int64)
        k = np.minimum(u, v) * self.n_nodos + np.maximum(u, v)
        pos = np.clip(np.searchsorted(self.claves, k), 0, max(len(self.claves) - 1, 0))
        ok = (self.claves[pos] == k) if len(self.claves) else np.zeros(len(k), bool)
        return [self.nombres[self.nombre[p]] if o else None for p, o in zip(pos, ok)]

    def principal(self, ctx: GraphContext, aristas) -> list[str]:
        """Nombres por metros recorridos, de más a menos (sólo los que pesan ≥ 15 %)."""
        g = ctx.graph
        metros: dict[str, float] = {}
        total = 0.0
        for e, n in zip(aristas, self.de_aristas(ctx, aristas)):
            total += float(g.elen[e])
            if n:
                metros[n] = metros.get(n, 0.0) + float(g.elen[e])
        orden = sorted(metros.items(), key=lambda x: -x[1])
        return [n for n, m in orden if m >= 0.15 * max(total, 1.0)]

    def del_nodo(self, ctx: GraphContext, nodo: int, excluir: set[str]) -> str | None:
        """Nombre de una vía que llega al nodo y no es la del tramo."""
        g = ctx.graph
        es = list(range(g.indptr[nodo], g.indptr[nodo + 1]))
        for n in self.de_aristas(ctx, es):
            if n and n not in excluir:
                return n
        return None


def _construir(ctx: GraphContext, data: dict) -> TablaNombres:
    g = ctx.graph
    orden = np.argsort(g.node_ids)
    ids_ord = g.node_ids[orden]
    us, vs, ns = [], [], []
    for i_nombre, _hw, nodos in data["vias"]:
        ids = np.asarray(nodos, dtype=np.int64)
        pos = np.clip(np.searchsorted(ids_ord, ids), 0, len(ids_ord) - 1)
        idx = np.where(ids_ord[pos] == ids, orden[pos], -1)
        a, b = idx[:-1], idx[1:]
        ok = (a >= 0) & (b >= 0)
        us.append(a[ok])
        vs.append(b[ok])
        ns.append(np.full(int(ok.sum()), i_nombre, dtype=np.int32))
    n = g.n_nodes
    if us:
        u, v = np.concatenate(us).astype(np.int64), np.concatenate(vs).astype(np.int64)
        claves = np.minimum(u, v) * n + np.maximum(u, v)
        nombre = np.concatenate(ns)
        o = np.argsort(claves, kind="stable")
        claves, nombre = claves[o], nombre[o]
    else:
        claves, nombre = np.zeros(0, np.int64), np.zeros(0, np.int32)
    return TablaNombres(claves=claves, nombre=nombre, nombres=data["nombres"],
                        puntos=[tuple(p) for p in data.get("puntos", [])],
                        parques=data.get("parques", []), n_nodos=n)


def _descargar(comuna: str) -> Path | None:
    dest = _DIR / f"{comuna}.nombres.json.gz"
    if not NOMBRES_URL and not dest.exists():
        return None
    if dest.exists() and time.time() - dest.stat().st_mtime < REFRESCO_S:
        return dest
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(f"{NOMBRES_URL}/{comuna}.nombres.json.gz",
                                     headers={"User-Agent": "PacePath/0.1"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(r.read())
            tmp.replace(dest)
        return dest
    except Exception as exc:  # noqa: BLE001 — sin nombres se sigue igual
        print(f"[nombres] sin nombres para «{comuna}»: {exc}")
        return dest if dest.exists() else None


_CACHE: dict[str, TablaNombres | None] = {}


def tabla(ctx: GraphContext) -> TablaNombres | None:
    clave = ctx.version
    with _lock:
        if clave in _CACHE:
            return _CACHE[clave]
        f = _descargar(ctx.region_id)
        t = None
        if f is not None:
            def leer():
                with gzip.open(f, "rt", encoding="utf-8") as fh:
                    return _construir(ctx, json.load(fh))
            from .disco import en_disco
            t = en_disco("nombres", ctx, leer)
        _CACHE.clear()          # una comuna en memoria, como el grafo
        _CACHE[clave] = t
        return t
