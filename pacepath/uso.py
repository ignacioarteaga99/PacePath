"""Métricas de uso anónimas para la beta: qué se usa y qué falla.

Sólo contadores por día y evento («ruta · series · providencia · ok»), en
memoria, y una línea en el log del servidor (Render). Nunca coordenadas, IP,
nombres ni datos de Strava. Se ven en /api/uso con la clave de PACEPATH_ADMIN.
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter

_lock = threading.Lock()
_CUENTA: Counter = Counter()
_DESDE = time.time()
MAX_CLAVES = 5000            # tope de memoria: las combinaciones raras se juntan


def registrar(evento: str, *detalle: str | None) -> None:
    dia = time.strftime("%Y-%m-%d", time.gmtime())
    partes = tuple(str(d)[:60] for d in detalle if d)
    with _lock:
        clave = (dia, evento, *partes)
        if clave not in _CUENTA and len(_CUENTA) >= MAX_CLAVES:
            clave = (dia, evento, "otros")
        _CUENTA[clave] += 1
    print("[uso] " + json.dumps({"evento": evento, "detalle": partes}, ensure_ascii=False), flush=True)


def resumen() -> dict:
    with _lock:
        filas = sorted(_CUENTA.items(), key=lambda x: (x[0][0], -x[1]), reverse=False)
    por_evento: Counter = Counter()
    for k, n in filas:
        por_evento[k[1]] += n
    return {"desde": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(_DESDE)),
            "totales": dict(por_evento.most_common()),
            "detalle": [{"dia": k[0], "evento": k[1], "detalle": " · ".join(k[2:]), "veces": n} for k, n in filas]}
