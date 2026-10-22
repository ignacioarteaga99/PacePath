"""Métricas de uso anónimas para la beta: qué se usa y qué falla.

Dos niveles:
  · Contadores por día y evento («ruta · series · providencia»), en memoria
    (se ven en /api/uso con la clave de PACEPATH_ADMIN; se pierden al dormir).
  · El registro de cada consulta (`registro`, panel /admin): el middleware de
    la API abre una `consulta` por pedido y `registrar` le agrega qué pasó
    (tipo de sesión, comuna, si salió bien).
Nunca coordenadas, IP, nombres ni datos de Strava.
"""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from contextvars import ContextVar

_lock = threading.Lock()
_CUENTA: Counter = Counter()
_DESDE = time.time()
MAX_CLAVES = 5000            # tope de memoria: las combinaciones raras se juntan

# La consulta en curso (un dict que el middleware crea y luego guarda).
consulta: ContextVar[dict | None] = ContextVar("consulta", default=None)


def registrar(evento: str, *detalle: str | None, tipo: str | None = None,
              comuna: str | None = None, exito: bool | None = None) -> None:
    dia = time.strftime("%Y-%m-%d", time.gmtime())
    partes = tuple(str(d)[:60] for d in (tipo, comuna, *detalle) if d)
    with _lock:
        clave = (dia, evento, *partes)
        if clave not in _CUENTA and len(_CUENTA) >= MAX_CLAVES:
            clave = (dia, evento, "otros")
        _CUENTA[clave] += 1
    print("[uso] " + json.dumps({"evento": evento, "detalle": partes}, ensure_ascii=False), flush=True)
    info = consulta.get()
    if info is not None:
        info["evento"] = evento
        info["tipo"] = tipo or info.get("tipo")
        info["comuna"] = comuna or info.get("comuna")
        resto = " · ".join(str(d) for d in detalle if d)
        if resto:
            info["detalle"] = resto
        if exito is not None:
            info["exito"] = exito


def resumen() -> dict:
    with _lock:
        filas = sorted(_CUENTA.items(), key=lambda x: (x[0][0], -x[1]), reverse=False)
    por_evento: Counter = Counter()
    for k, n in filas:
        por_evento[k[1]] += n
    return {"desde": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(_DESDE)),
            "totales": dict(por_evento.most_common()),
            "detalle": [{"dia": k[0], "evento": k[1], "detalle": " · ".join(k[2:]), "veces": n} for k, n in filas]}
