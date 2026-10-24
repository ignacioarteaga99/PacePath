"""PacePath — la ruta para el entrenamiento de hoy, sobre calles reales.

RaceEngine lee `RACEENGINE_GRAPHS_URL` y `RACEENGINE_DATA` AL IMPORTARSE, así
que los valores por defecto se fijan aquí, antes de que nada lo importe.
"""
import os
from pathlib import Path

GRAPHS_URL = "https://github.com/ignacioarteaga99/RaceEngine/releases/download/graphs"

os.environ.setdefault("RACEENGINE_GRAPHS_URL", GRAPHS_URL)
os.environ.setdefault("RACEENGINE_DATA", str(Path(__file__).resolve().parents[1] / "data"))
# Render gratis: 512 MB. Una comuna generando ocupa 95–170 MB; dos a la vez ya
# es arriesgado, así que por defecto sólo una en memoria.
os.environ.setdefault("RACEENGINE_MAX_REGIONS", "1")
