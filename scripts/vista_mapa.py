"""Fotos del mapa propio (estilo PacePath) para revisarlo antes de publicarlo.

    python scripts/vista_mapa.py <url base del mapa> <carpeta de salida>

La url base es la de un servidor (scripts/servidor_rango.py) que sirve la
carpeta de salida, donde ya están chile.pmtiles y fonts/: ahí se copian la
página y las librerías. Usa las librerías de web/vendor/mapa y el estilo
de web/mapa/estilo.js, igual que la app.
"""
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

WEB = pathlib.Path(__file__).resolve().parents[1] / "web"
BASE, SALIDA = sys.argv[1].rstrip("/"), pathlib.Path(sys.argv[2])
SALIDA.mkdir(parents=True, exist_ok=True)
VISTAS = [  # nombre, centro (lat, lon), zoom, noche
    ("providencia_dia", (-33.4300, -70.6150), 14.5, False),
    ("providencia_noche", (-33.4300, -70.6150), 14.5, True),
    ("san_cristobal", (-33.4230, -70.6330), 14, False),
    ("santiago", (-33.4450, -70.6300), 11.5, False),
    ("cajon_del_maipo", (-33.6400, -70.3500), 11, False),
    ("chile_centro", (-34.5, -71.0), 6.5, False),
]
PAGINA = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body,#m{margin:0;height:100%}</style>
<script src="libs/maplibre-gl.js"></script><script src="libs/pmtiles.js"></script>
<script src="libs/maplibre-contour.min.js"></script><script src="libs/estilo.js"></script></head><body><div id="m"></div><script>
maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
const p = new URLSearchParams(location.hash.slice(1));
const m = new maplibregl.Map({ container: "m", attributionControl: false, fadeDuration: 0,
  center: [+p.get("lon"), +p.get("lat")], zoom: +p.get("z"),
  style: estiloPacePath({ base: "{base}", noche: p.get("noche") === "1", maplibregl }) });
m.on("error", (e) => (window.errores ||= []).push(String(e.error && e.error.message || e.error)));
m.on("idle", () => setTimeout(() => document.body.dataset.listo = "1", 500));
</script></body></html>"""

(SALIDA / "libs").mkdir(exist_ok=True)
for f in ["maplibre-gl.js", "pmtiles.js", "maplibre-contour.min.js"]:
    (SALIDA / "libs" / f).write_bytes((WEB / "vendor/mapa" / f).read_bytes())
(SALIDA / "libs/estilo.js").write_bytes((WEB / "mapa/estilo.js").read_bytes())
(SALIDA / "vista.html").write_text(PAGINA.replace("{base}", BASE))
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
    for nombre, (lat, lon), z, noche in VISTAS:
        pg = b.new_page(viewport={"width": 390, "height": 720}, device_scale_factor=2)
        pg.goto(f"{BASE}/vista.html#lat={lat}&lon={lon}&z={z}&noche={int(noche)}")
        try:
            pg.wait_for_selector("body[data-listo]", timeout=90_000)
        except Exception:
            print(nombre, "no terminó de cargar en 90 s")
        pg.screenshot(path=str(SALIDA / f"{nombre}.png"))
        print(nombre, "errores:", (pg.evaluate("window.errores || []"))[:3])
        pg.close()
    b.close()
