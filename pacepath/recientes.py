"""Rutas recién calculadas, para responder al tiro si se piden de nuevo.

Pasa más de lo que parece: volver a abrir una ruta, «Otra opción» y volver a
la primera, el GPX después de ver la ruta, dos personas que salen del mismo
parque con la misma sesión. La clave es todo lo que cambia el resultado: el
pedido (salida redondeada a ~10 m, sesión, forma, tope de subida, calles
preferidas…), el mapa (su versión), el momento (noche, calor, hora punta) y
el idioma de los textos. Se guarda comprimido (una ruta pesa ~30 KB así) y
vence a las 6 horas.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import zlib
from collections import OrderedDict

MAX_RUTAS = 64
VENCE_S = 6 * 3600

_rutas: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
_lock = threading.Lock()
aciertos = 0
fallos = 0


def _json(o):
    return o.item() if hasattr(o, "item") else str(o)         # numpy → número de Python


def clave(pedido: dict, version: str, momento: tuple | None, idioma: str) -> str:
    p = dict(pedido)
    for k in ("lat", "lon"):
        if p.get(k) is not None:
            p[k] = round(p[k], 4)                    # ~10 m: el GPS baila
    if isinstance(p.get("meta"), dict):
        p["meta"] = {k: round(v, 4) for k, v in p["meta"].items()}
    p.pop("hora", None)                             # lo que importa de la hora va en `momento`
    texto = json.dumps([p, version, momento, idioma], sort_keys=True, separators=(",", ":"), default=_json)
    return hashlib.sha256(texto.encode()).hexdigest()


def buscar(k: str) -> dict | None:
    global aciertos, fallos
    with _lock:
        x = _rutas.get(k)
        if x and time.time() - x[0] < VENCE_S:
            _rutas.move_to_end(k)
            aciertos += 1
            dato = x[1]
        else:
            if x:
                _rutas.pop(k, None)
            fallos += 1
            return None
    return json.loads(zlib.decompress(dato))


def guardar(k: str, plan: dict) -> None:
    dato = zlib.compress(json.dumps(plan, separators=(",", ":"), default=_json).encode(), 6)
    with _lock:
        _rutas[k] = (time.time(), dato)
        _rutas.move_to_end(k)
        while len(_rutas) > MAX_RUTAS:
            _rutas.popitem(last=False)


def olvidar() -> None:
    global aciertos, fallos
    with _lock:
        _rutas.clear()
        aciertos = fallos = 0


def estado() -> dict:
    with _lock:
        return {"rutas": len(_rutas), "kb": round(sum(len(d) for _, d in _rutas.values()) / 1024),
                "aciertos": aciertos, "fallos": fallos}
