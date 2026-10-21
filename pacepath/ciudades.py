"""Dónde corriste: ciudad y país de un punto, sin servicios externos.

Usa las ciudades de GeoNames con ≥ 15 000 habitantes (`datos/ciudades.json.gz`,
generado con `scripts/ciudades.py`; CC BY 4.0). Las coordenadas del corredor no
salen de nuestro servidor ni se guardan.

La ciudad de un punto no es la más cercana sino la más importante cerca: una
carrera en Providencia o Las Condes es «Santiago», no la comuna. Puntaje:
población / (1 + (d / 5 km)²), dentro de 40 km.
"""
from __future__ import annotations

import gzip
import json
import math
import threading
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

ARCHIVO = Path(__file__).parent / "datos" / "ciudades.json.gz"
RADIO_KM = 40.0
ESCALA_KM = 5.0
LEJOS_KM = 150.0          # más allá no se nombra nada
# Lejos de toda ciudad (cerros, parques nacionales) se dice «cerca de X»: sin
# fronteras en los datos, el país es el de esa ciudad y en zonas de frontera
# puede no ser el del punto (Torres del Paine → cerca de El Calafate).
R_TIERRA_KM = 6371.0

_lock = threading.Lock()
_DATOS: tuple | None = None


def _xyz(lat, lon) -> np.ndarray:
    la, lo = np.radians(lat), np.radians(lon)
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def _datos():
    global _DATOS
    with _lock:
        if _DATOS is None:
            with gzip.open(ARCHIVO, "rt", encoding="utf-8") as f:
                filas = json.load(f)["ciudades"]
            nombres = [r[0] for r in filas]
            paises = [r[1] for r in filas]
            lat = np.array([r[2] for r in filas]); lon = np.array([r[3] for r in filas])
            pob = np.array([r[4] for r in filas], dtype=np.float64)
            _DATOS = (cKDTree(_xyz(lat, lon)), nombres, paises, pob)
        return _DATOS


def _km(cuerda: np.ndarray) -> np.ndarray:
    return 2 * R_TIERRA_KM * np.arcsin(np.clip(cuerda / 2, 0, 1))


def nombrar(lat: float, lon: float) -> dict | None:
    """{"ciudad", "pais" (ISO de 2 letras), "cerca" (lejos de toda ciudad)} o None."""
    arbol, nombres, paises, pob = _datos()
    p = _xyz([lat], [lon])[0]
    idx = arbol.query_ball_point(p, 2 * math.sin(RADIO_KM / R_TIERRA_KM / 2))
    if idx:
        idx = np.asarray(idx)
        d = _km(np.linalg.norm(arbol.data[idx] - p, axis=1))
        i = int(idx[np.argmax(pob[idx] / (1 + (d / ESCALA_KM) ** 2))])
        return {"ciudad": nombres[i], "pais": paises[i], "cerca": False}
    cuerda, i = arbol.query(p)
    if _km(np.array([cuerda]))[0] <= LEJOS_KM:
        return {"ciudad": nombres[int(i)], "pais": paises[int(i)], "cerca": True}
    return None
