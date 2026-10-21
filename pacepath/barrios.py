"""¿En qué comuna (barrio) está un punto? Límites de OpenStreetMap.

Los publica el workflow «Barrios» en la Release «nombres» (barrios_cl.json.gz:
por ahora Región Metropolitana y Valparaíso). Fuera de esas regiones, o si aún
no se descargan, `barrio` devuelve None y se usa el lugar más cercano de
GeoNames (ver `ciudades`).
"""
from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.request

import numpy as np

from .nombres import NOMBRES_URL, REFRESCO_S, _DIR

ARCHIVO = "barrios_cl.json.gz"
_lock = threading.Lock()
_DATOS: list | None = None
_CARGADO = 0.0


def _bajar():
    dest = _DIR / ARCHIVO
    if dest.exists() and time.time() - dest.stat().st_mtime < REFRESCO_S:
        return dest
    if not NOMBRES_URL:
        return dest if dest.exists() else None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(f"{NOMBRES_URL}/{ARCHIVO}", headers={"User-Agent": "PacePath/0.1"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(r.read())
            tmp.replace(dest)
    except Exception as exc:  # noqa: BLE001 — sin barrios, se usa GeoNames
        print(f"[barrios] no pude bajar {ARCHIVO}: {exc}")
    return dest if dest.exists() else None


def _datos() -> list:
    """[(nombre, pais, caja, [anillos como arrays (n, 2)])]"""
    global _DATOS, _CARGADO
    with _lock:
        if _DATOS is None or time.time() - _CARGADO > REFRESCO_S:
            f = _bajar()
            datos = []
            if f is not None:
                with gzip.open(f, "rt", encoding="utf-8") as fh:
                    for b in json.load(fh).get("barrios", []):
                        datos.append((b["nombre"], b.get("pais"), b["caja"],
                                      [np.asarray(r, dtype=np.float64) for r in b["anillos"]]))
            _DATOS, _CARGADO = datos, time.time()
        return _DATOS


def _dentro(lat: float, lon: float, r: np.ndarray) -> bool:
    """Ray casting (par/impar) sobre un anillo [[lat, lon], ...]."""
    y, x = r[:, 0], r[:, 1]
    y2, x2 = np.roll(y, -1), np.roll(x, -1)
    cruza = (y > lat) != (y2 > lat)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = x + (lat - y) * (x2 - x) / (y2 - y)
    return bool(np.count_nonzero(cruza & (lon < xc)) % 2)


def barrio(lat: float, lon: float) -> str | None:
    for nombre, _, (a0, o0, a1, o1), anillos in _datos():
        if a0 <= lat <= a1 and o0 <= lon <= o1 and any(_dentro(lat, lon, r) for r in anillos):
            return nombre
    return None
