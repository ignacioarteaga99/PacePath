"""Límites de comunas (barrios para el Pasaporte), desde Overpass (en CI).

GeoNames no trae como lugares a Las Condes, Vitacura, La Reina, Macul…: para
saber en qué comuna empieza una carrera hacen falta sus límites. Se bajan de
OpenStreetMap (admin_level=8) por región, se simplifican (~15 m) y se publican:

    barrios_cl.json.gz = {"fuente", "fecha",
        "barrios": [{"nombre", "pais", "caja": [lat0, lon0, lat1, lon1],
                     "anillos": [[[lat, lon], ...], ...]}, ...]}

    python scripts/ci_barrios.py dist/ CL-RM,CL-VS
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ci_nombres import anillos, overpass

TOLERANCIA = 0.00015        # grados (~15 m)


def _rdp(p: list, tol: float) -> list:
    """Douglas-Peucker (iterativo) sobre [[lat, lon], ...]. Un anillo cerrado se
    parte en su punto más lejano al inicio (si no, el segmento inicio–fin mide 0)."""
    if len(p) < 4:
        return p
    if p[0] == p[-1]:
        m = max(range(len(p)), key=lambda i: (p[i][0] - p[0][0]) ** 2 + (p[i][1] - p[0][1]) ** 2)
        if 0 < m < len(p) - 1:
            return _rdp(p[:m + 1], tol) + _rdp(p[m:], tol)[1:]
    guardar = [False] * len(p)
    guardar[0] = guardar[-1] = True
    pila = [(0, len(p) - 1)]
    while pila:
        a, b = pila.pop()
        (y1, x1), (y2, x2) = p[a], p[b]
        dx, dy = x2 - x1, y2 - y1
        n = (dx * dx + dy * dy) ** .5 or 1e-12
        mejor, imax = 0.0, -1
        for i in range(a + 1, b):
            y, x = p[i]
            d = abs(dy * (x - x1) - dx * (y - y1)) / n
            if d > mejor:
                mejor, imax = d, i
        if mejor > tol and imax > 0:
            guardar[imax] = True
            pila += [(a, imax), (imax, b)]
    return [q for q, g in zip(p, guardar) if g]


def main(salida: Path, regiones: list[str]) -> None:
    filtro = "|".join(regiones)
    q = f"""[out:json][timeout:900];
area["boundary"="administrative"]["ISO3166-2"~"^({filtro})$"]->.r;
relation(area.r)["boundary"="administrative"]["admin_level"="8"];
out geom;"""
    data = overpass(q)
    barrios = []
    for el in data.get("elements", []):
        nombre = (el.get("tags") or {}).get("name")
        if el.get("type") != "relation" or not nombre:
            continue
        rs = [[[round(a, 5), round(b, 5)] for a, b in _rdp(r, TOLERANCIA)] for r in anillos(el.get("members", []))]
        rs = [r for r in rs if len(r) >= 4]
        if not rs:
            continue
        lats = [p[0] for r in rs for p in r]; lons = [p[1] for r in rs for p in r]
        barrios.append({"nombre": nombre, "pais": regiones[0][:2],
                        "caja": [min(lats), min(lons), max(lats), max(lons)], "anillos": rs})
    salida.mkdir(parents=True, exist_ok=True)
    dest = salida / "barrios_cl.json.gz"
    with gzip.open(dest, "wt", encoding="utf-8") as f:
        json.dump({"fuente": "© OpenStreetMap contributors (ODbL)",
                   "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "barrios": barrios}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"barrios: {len(barrios)} comunas ({', '.join(sorted(b['nombre'] for b in barrios)[:8])}…) "
          f"→ {dest.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "dist"),
         (sys.argv[2] if len(sys.argv) > 2 else "CL-RM,CL-VS").split(","))
