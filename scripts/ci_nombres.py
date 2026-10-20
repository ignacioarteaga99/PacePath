"""Nombres de calle y puntos de agua de una comuna, desde Overpass (en CI).

Los grafos de RaceEngine no guardan el nombre de las vías, y para explicar una
ruta hace falta («las series van por Av. Andrés Bello»). Mientras eso no esté en
el grafo (propuesta para RaceEngine: un `ename` por arista), se publica aparte:

    <comuna>.nombres.json.gz = {
        "comuna", "fecha", "nombres": [str],
        "vias":   [[indice_nombre, highway, [id_nodo_osm, ...]], ...],
        "puntos": [[tipo, lat, lon], ...],      # drinking_water, toilets
        "lugares": [[nombre, lat, lon, tipo], ...],  # calle, esquina, parque
        "parques": [[[lat, lon], ...], ...]          # polígonos de parques y jardines
    }

`parques` existe porque el grafo marca muy pocas aristas como «en parque»
(814 de 140.000 sendas en Providencia): con los polígonos, PacePath marca
las sendas de Parque Balmaceda, Bustamante, etc.

`lugares` sirve para buscar desde dónde sale el corredor sin depender de un
geocodificador externo: cada calle (punto medio de su tramo más largo), cada
esquina con nombre («Pocuro con Tobalaba») y cada parque con nombre.

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


def anillos(miembros: list[dict]) -> list[list[list[float]]]:
    """Une los tramos exteriores de una relación (multipolígono) en anillos.

    Parque Balmaceda, el Parque de las Esculturas o Inés de Suárez no son una
    vía cerrada en OSM sino una relación de varias vías: sin esto quedaban
    fuera y la ribera del Mapocho no contaba como parque."""
    tramos = [[(round(p["lat"], 5), round(p["lon"], 5)) for p in m["geometry"]]
              for m in miembros
              if m.get("type") == "way" and m.get("role", "outer") in ("outer", "")
              and m.get("geometry")]
    out = []
    while tramos:
        anillo = tramos.pop(0)
        while anillo[0] != anillo[-1]:
            for i, t in enumerate(tramos):
                if t[0] == anillo[-1]:
                    anillo += t[1:]
                elif t[-1] == anillo[-1]:
                    anillo += t[-2::-1]
                else:
                    continue
                tramos.pop(i)
                break
            else:
                break           # no cierra: se usa igual (el ray casting lo cierra)
        if len(anillo) >= 4:
            out.append([list(p) for p in anillo])
    return out


def main(comuna: str, salida: Path) -> None:
    man = {e["id"]: e for e in json.loads(_get(MANIFEST))["regions"]}
    e = man[comuna]
    lat, lon, r = e["lat"], e["lon"], e.get("radius_m", 7000)
    q = f"""[out:json][timeout:600];
(
  way["highway"]["name"](around:{r},{lat},{lon});
  node["amenity"~"^(drinking_water|toilets)$"](around:{r},{lat},{lon});
);
out body geom;
way["leisure"="park"]["name"](around:{r},{lat},{lon});
out center tags;
way["leisure"~"^(park|garden)$"](around:{r},{lat},{lon});
out geom;
relation["leisure"~"^(park|garden)$"](around:{r},{lat},{lon});
out geom;"""
    data = overpass(q)
    nombres: list[str] = []
    idx: dict[str, int] = {}
    vias, puntos, lugares, parques = [], [], [], []
    mitad: dict[str, tuple[float, float, float]] = {}      # nombre → (largo, lat, lon)
    nombres_nodo: dict[int, set[str]] = {}
    coord_nodo: dict[int, tuple[float, float]] = {}
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "relation":
            if tags.get("leisure") in ("park", "garden"):
                parques.extend(anillos(el.get("members", [])))
                b = el.get("bounds")
                if tags.get("name") and b:
                    lugares.append([tags["name"], round((b["minlat"] + b["maxlat"]) / 2, 6),
                                    round((b["minlon"] + b["maxlon"]) / 2, 6), "parque"])
            continue
        if el["type"] == "way" and tags.get("leisure") in ("park", "garden"):
            c = el.get("center")
            if c:
                lugares.append([tags["name"], round(c["lat"], 6), round(c["lon"], 6), "parque"])
            elif el.get("geometry") and len(el["geometry"]) >= 4:
                parques.append([[round(p["lat"], 5), round(p["lon"], 5)] for p in el["geometry"]])
            continue
        if el["type"] == "way":
            n = tags["name"]
            geom = el.get("geometry") or []
            if geom:
                largo = len(geom)
                m = geom[len(geom) // 2]
                if largo > mitad.get(n, (0, 0, 0))[0]:
                    mitad[n] = (largo, m["lat"], m["lon"])
                for nid, g in zip(el["nodes"], geom):
                    nombres_nodo.setdefault(nid, set()).add(n)
                    coord_nodo[nid] = (g["lat"], g["lon"])
            if n not in idx:
                idx[n] = len(nombres)
                nombres.append(n)
            vias.append([idx[n], tags.get("highway", ""), el["nodes"]])
        elif el["type"] == "node":
            puntos.append([tags["amenity"], round(el["lat"], 6), round(el["lon"], 6)])
    for n, (_, la, lo) in mitad.items():
        lugares.append([n, round(la, 6), round(lo, 6), "calle"])
    vistas = set()
    for nid, ns in nombres_nodo.items():
        if len(ns) < 2:
            continue
        orden = sorted(ns)
        for i, a in enumerate(orden):
            for b in orden[i + 1:]:
                if (a, b) in vistas:
                    continue
                vistas.add((a, b))
                la, lo = coord_nodo[nid]
                lugares.append([f"{a} con {b}", round(la, 6), round(lo, 6), "esquina"])
    out = {
        "comuna": comuna,
        "fecha": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "grafo": e.get("version", ""),
        "nombres": nombres, "vias": vias, "puntos": puntos, "lugares": lugares,
        "parques": parques,
    }
    salida.mkdir(parents=True, exist_ok=True)
    f = salida / f"{comuna}.nombres.json.gz"
    with gzip.open(f, "wt", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"{comuna}: {len(vias)} vías, {len(nombres)} nombres, {len(puntos)} puntos, "
          f"{len(lugares)} lugares "
          f"→ {f} ({f.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2] if len(sys.argv) > 2 else "dist"))
