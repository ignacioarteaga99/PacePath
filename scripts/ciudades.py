"""Genera pacepath/datos/ciudades.json.gz: ciudades de GeoNames con ≥ 15 000 habitantes.

Sirve para nombrar dónde corriste (ciudad y país) sin mandar tus coordenadas a
ningún servicio externo. Se corre a mano cuando se quiera actualizar:

    pip install geonamescache && python scripts/ciudades.py

Datos: GeoNames (https://www.geonames.org), licencia CC BY 4.0.
"""
import gzip
import json
from pathlib import Path

import geonamescache

filas = sorted(
    ([c["name"], c["countrycode"], round(c["latitude"], 4), round(c["longitude"], 4),
      int(c["population"])]
     for c in geonamescache.GeonamesCache(min_city_population=15000).get_cities().values()),
    key=lambda f: -f[4])
# Continente de cada país (AF, AN, AS, EU, NA, OC, SA), para el Pasaporte.
continentes = {cc: c["continentcode"] for cc, c in geonamescache.GeonamesCache().get_countries().items()}
destino = Path(__file__).resolve().parent.parent / "pacepath" / "datos" / "ciudades.json.gz"
with gzip.open(destino, "wt", encoding="utf-8") as f:
    json.dump({"fuente": "GeoNames (geonames.org), CC BY 4.0",
               "campos": ["nombre", "pais", "lat", "lon", "poblacion"], "ciudades": filas,
               "continentes": continentes},
              f, ensure_ascii=False, separators=(",", ":"))
print(len(filas), "ciudades →", destino, destino.stat().st_size // 1024, "KB")
