"""Comunas disponibles (grafos de la Release «graphs» de RaceEngine)."""
from __future__ import annotations

import math

import pacepath  # noqa: F401 — fija las variables de entorno de RaceEngine
from raceengine_engine.context import GraphContext, get_context
from raceengine_geodata import remote

# Las que Ignacio pidió para empezar. El manifiesto puede traer más.
COMUNAS = ("santiago_centro", "providencia", "las_condes", "nunoa", "vitacura",
           "lo_barnechea")


def disponibles() -> list[dict]:
    """Comunas del manifiesto, con centro y radio, sin cargar ningún grafo."""
    man = remote.manifest()
    return [
        {"id": rid, "nombre": e.get("label", rid), "lat": e["lat"], "lon": e["lon"],
         "radio_m": e.get("radius_m", 7000), "mb": round(e.get("size_gz", 0) / 1e6, 1)}
        for rid, e in man.items() if "lat" in e
    ]


def _dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon2 - lon1) * k, lat2 - lat1) * 111_195.0


def comuna_para(lat: float, lon: float) -> str | None:
    """El grafo donde el punto queda más adentro: el de mayor margen hasta su
    borde (radio − distancia al centro). Cada grafo es un círculo y se solapan;
    con radios distintos (Las Condes y Santiago Centro miden 9 km, el resto 7)
    «el centro más cercano» podía dejar al corredor a 2 km del borde teniendo
    otro grafo con 5 km de calle para todos lados. Más margen = más rutas
    posibles, sobre todo para un fondo."""
    mejor, mejor_margen = None, 0.0
    for c in disponibles():
        margen = c["radio_m"] - _dist_m(lat, lon, c["lat"], c["lon"])
        if margen >= 0 and (mejor is None or margen > mejor_margen):
            mejor, mejor_margen = c["id"], margen
    return mejor


def contexto(comuna: str) -> GraphContext:
    """Grafo + A* + arrays derivados de RaceEngine (descarga la primera vez)."""
    return get_context(comuna)
