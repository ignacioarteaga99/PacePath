"""PacePath — la ruta para el entrenamiento de hoy, sobre calles reales.

RaceEngine lee `RACEENGINE_GRAPHS_URL` y `RACEENGINE_DATA` AL IMPORTARSE, así
que los valores por defecto se fijan aquí, antes de que nada lo importe.
"""
import os
import urllib.request
from pathlib import Path

# Las descargas sin nombre propio (las de RaceEngine: grafos y manifiesto) salen
# como «Python-urllib», y Cloudflare (R2 en mapas.pacepathapp.com) las rechaza
# con un 403 (error 1010, «verificación de integridad del navegador»). Con un
# User-Agent propio pasan. Las que ya traen uno (Strava, Nominatim) lo conservan.
_opener = urllib.request.build_opener()
_opener.addheaders = [("User-Agent", "PacePath/0.1 (https://pacepathapp.com)")]
urllib.request.install_opener(_opener)

# Grafos en Cloudflare R2 (los copia desde RaceEngine el workflow «Espejo de datos en R2»).
GRAPHS_URL = "https://mapas.pacepathapp.com/graphs"

os.environ.setdefault("RACEENGINE_GRAPHS_URL", GRAPHS_URL)
os.environ.setdefault("RACEENGINE_DATA", str(Path(__file__).resolve().parents[1] / "data"))
# Render gratis: 512 MB. Una comuna generando ocupa 95–170 MB; dos a la vez ya
# es arriesgado, así que por defecto sólo una en memoria.
os.environ.setdefault("RACEENGINE_MAX_REGIONS", "1")
