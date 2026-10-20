"""Nombres de calle y puntos de agua de una comuna, desde Overpass (en CI).

Los grafos de RaceEngine no guardan el nombre de las vías, y para explicar una
ruta hace falta («las series van por Av. Andrés Bello»). Mientras eso no esté en
el grafo (propuesta para RaceEngine: un `ename` por arista), se publica aparte:

    <comuna>.nombres.json.gz = {
        "comuna", "fecha", "nombres": [str],
        "vias":   [[indice_nombre, highway, [id_nodo_osm, ...]], ...],
        "puntos": [[tipo, lat, lon], ...]       # drinking_water, toilets
    }

Se usa el mismo centro y radio que el grafo (manifiesto de RaceEngine), y los
ids de nodo de OSM, que el grafo también guarda: así cada arista del grafo
encuentra su nombre sin comparar geometrías.

    python scripts/ci_nombres.py providencia dist/
"""
from __future__ import annotations

import gzip
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MANIFEST = "https://github.com/ignacioarteaga99/RaceEngine/releases/download/graphs/manifest.json"
OVERPASS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
UA = "PacePath/0.1 (https://github.com/ignacioarteaga99/PacePath)"


def _get(url: str, data: bytes | None = None, timeout: int = 300) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def overpass(query: str) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    for intento in range(6):
        url = OVERPASS[intento % len(OVERPASS)]
        try:
            print(f"  [overpass] {url} (intento {intento + 1})", flush=True)
            return json.loads(_get(url, body))
        except Exception as exc:  # noqa: BLE001
            print(f"  [overpass] falló: {exc}", flush=True)
            time.sleep(10 * (intento + 1))
    raise RuntimeError("Overpass no respondió")


def main(comuna: str, salida: Path) -> None:
    man = {e["id"]: e for e in json.loads(_get(MANIFEST))["regions"]}
    e = man[comuna]
    lat, lon, r = e["lat"], e["lon"], e.get("radius_m", 7000)
    q = f"""[out:json][timeout:600];
(
  way["highway"]["name"](around:{r},{lat},{lon});
  node["amenity"~"^(drinking_water|toilets)$"](around:{r},{lat},{lon});
);
out body;"""
    data = overpass(q)
    nombres: list[str] = []
    idx: dict[str, int] = {}
    vias, puntos = [], []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "way":
            n = tags["name"]
            if n not in idx:
                idx[n] = len(nombres)
                nombres.append(n)
            vias.append([idx[n], tags.get("highway", ""), el["nodes"]])
        elif el["type"] == "node":
            puntos.append([tags["amenity"], round(el["lat"], 6), round(el["lon"], 6)])
    out = {
        "comuna": comuna,
        "fecha": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "grafo": e.get("version", ""),
        "nombres": nombres, "vias": vias, "puntos": puntos,
    }
    salida.mkdir(parents=True, exist_ok=True)
    f = salida / f"{comuna}.nombres.json.gz"
    with gzip.open(f, "wt", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{comuna}: {len(vias)} vías, {len(nombres)} nombres, {len(puntos)} puntos "
          f"→ {f} ({f.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else "dist"))
