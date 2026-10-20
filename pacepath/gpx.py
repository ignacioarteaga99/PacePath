"""GPX de la sesión: un segmento por parte y una marca al inicio de cada serie."""
from __future__ import annotations

from xml.sax.saxutils import escape


def _trkpt(p) -> str:
    return f'<trkpt lat="{p[0]}" lon="{p[1]}"><ele>{p[2]}</ele></trkpt>'


def plan_a_gpx(plan: dict) -> str:
    resumen = plan["sesion"]["resumen"] if "sesion" in plan else "ruta trazada"
    nombre = f"PacePath · {resumen}"
    wpts, segs = [], []
    for parte in plan["partes"]:
        c = parte.get("coordenadas") or []
        if not c:
            continue
        if parte["tipo"] == "serie":
            k = parte["numero"]
            wpts.append(f'<wpt lat="{c[0][0]}" lon="{c[0][1]}"><name>Serie {k} · inicio</name></wpt>')
            wpts.append(f'<wpt lat="{c[-1][0]}" lon="{c[-1][1]}"><name>Serie {k} · fin</name></wpt>')
        segs.append("<trkseg>" + "".join(_trkpt(p) for p in c) + "</trkseg>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="PacePath" xmlns="http://www.topografix.com/GPX/1/1">'
        f"<metadata><name>{escape(nombre)}</name></metadata>"
        + "".join(wpts)
        + f"<trk><name>{escape(nombre)}</name>" + "".join(segs) + "</trk></gpx>\n"
    )
