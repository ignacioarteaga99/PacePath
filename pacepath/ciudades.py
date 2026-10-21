"""Dónde corriste: ciudad y país de un punto, sin servicios externos.

Usa las ciudades de GeoNames con ≥ 15 000 habitantes (`datos/ciudades.json.gz`,
generado con `scripts/ciudades.py`; CC BY 4.0). Las coordenadas del corredor no
salen de nuestro servidor ni se guardan.

La ciudad de un punto no es la más cercana sino la más importante cerca: una
carrera en Providencia o Las Condes es «Santiago», no la comuna. Puntaje:
población / (1 + (d / 5 km)²), dentro de 40 km.

El barrio es la comuna donde cae el punto, con los límites de OpenStreetMap
(`barrios`: Región Metropolitana y Valparaíso). En el resto del mundo, el lugar
de GeoNames más cercano (a menos de 8 km): en Nueva York, el distrito.

Antártica no tiene ciudades de 15 000 habitantes: al sur de los 60° S, el
continente es «AN» y no se nombra ciudad.
"""
from __future__ import annotations

import gzip
import json
import math
import threading
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from . import barrios

ARCHIVO = Path(__file__).parent / "datos" / "ciudades.json.gz"
RADIO_KM = 40.0
ESCALA_KM = 5.0
LEJOS_KM = 150.0          # más allá no se nombra nada
BARRIO_KM = 8.0
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
            with gzip.open(ARCHIVO, "rt", encoding="utf-8") as f:
                cont = json.load(f).get("continentes", {})
            _DATOS = (cKDTree(_xyz(lat, lon)), nombres, paises, pob, cont)
        return _DATOS


def _km(cuerda: np.ndarray) -> np.ndarray:
    return 2 * R_TIERRA_KM * np.arcsin(np.clip(cuerda / 2, 0, 1))


def nombrar(lat: float, lon: float) -> dict | None:
    """{"ciudad", "pais" (ISO de 2 letras), "continente", "barrio" (o None),
    "cerca" (lejos de toda ciudad)} o None."""
    if lat < -60:
        return {"ciudad": None, "pais": "AQ", "continente": "AN", "barrio": None, "cerca": True}
    arbol, nombres, paises, pob, cont = _datos()
    p = _xyz([lat], [lon])[0]
    cuerda, j = arbol.query(p)
    d_cerca = float(_km(np.array([cuerda]))[0])
    barrio = barrios.barrio(lat, lon) or (nombres[int(j)] if d_cerca <= BARRIO_KM else None)
    idx = arbol.query_ball_point(p, 2 * math.sin(RADIO_KM / R_TIERRA_KM / 2))
    if idx:
        idx = np.asarray(idx)
        d = _km(np.linalg.norm(arbol.data[idx] - p, axis=1))
        i = int(idx[np.argmax(pob[idx] / (1 + (d / ESCALA_KM) ** 2))])
        cerca = False
    elif d_cerca <= LEJOS_KM:
        i, cerca = int(j), True
    else:
        return None
    return {"ciudad": nombres[i], "pais": paises[i], "continente": cont.get(paises[i]),
            "barrio": barrio, "cerca": cerca}
