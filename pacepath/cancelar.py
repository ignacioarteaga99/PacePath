"""Cancelar una ruta mientras se calcula.

El teléfono manda cada pedido de ruta con un id (cabecera X-Pedido). Si la
persona toca «Cancelar», el teléfono corta la conexión y avisa a
/api/cancelar con ese id; la API también se da cuenta si la conexión se cae.
En ambos casos se marca el pedido, y el motor lo revisa entre etapas
(`revisar()`): deja de calcular y suelta el turno para el siguiente, en vez
de terminar una ruta que nadie va a ver.

El corte de conexión se escucha en la capa más externa (`VigilarConexion`):
los middlewares de Starlette de más adentro no dejan ver la desconexión
hasta que la respuesta ya salió, o sea, tarde.
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from contextvars import ContextVar

_actual: ContextVar[threading.Event | None] = ContextVar("cancelar", default=None)
_pedidos: dict[str, threading.Event] = {}
_lock = threading.Lock()
MAX_PEDIDOS = 500


class Cancelado(Exception):
    """La persona canceló (o se fue): no seguir calculando."""


def nuevo(pedido: str | None, ev: threading.Event | None = None) -> threading.Event:
    ev = ev or threading.Event()
    if pedido:
        with _lock:
            if len(_pedidos) >= MAX_PEDIDOS:          # ids que nunca terminaron: no acumular
                _pedidos.pop(next(iter(_pedidos)))
            _pedidos[pedido] = ev
    return ev


def soltar(pedido: str | None) -> None:
    if pedido:
        with _lock:
            _pedidos.pop(pedido, None)


def cancelar(pedido: str) -> bool:
    with _lock:
        ev = _pedidos.get(pedido)
    if ev is None:
        return False
    ev.set()
    return True


@contextmanager
def vigilar(ev: threading.Event):
    """Durante el bloque, `revisar()` mira este pedido."""
    marca = _actual.set(ev)
    try:
        yield
    finally:
        _actual.reset(marca)


def revisar() -> None:
    """Punto de corte: si cancelaron este pedido, se sale con Cancelado."""
    ev = _actual.get()
    if ev is not None and ev.is_set():
        raise Cancelado()


class VigilarConexion:
    """Middleware ASGI: en `rutas`, lee el pedido completo y se queda
    escuchando la conexión; si el teléfono corta, marca el evento que deja en
    scope["pp_corte"] (la API lo usa como el de ese pedido)."""

    def __init__(self, app, rutas: set[str]):
        self.app, self.rutas = app, rutas

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in self.rutas:
            return await self.app(scope, receive, send)
        mensajes = []
        while True:
            m = await receive()
            if m["type"] == "http.disconnect":
                return
            mensajes.append(m)
            if not m.get("more_body"):
                break
        ev, cortado = threading.Event(), asyncio.Event()
        scope["pp_corte"] = ev

        async def escuchar():
            while (await receive())["type"] != "http.disconnect":
                pass
            ev.set()
            cortado.set()

        async def recibir():
            if mensajes:
                return mensajes.pop(0)
            await cortado.wait()
            return {"type": "http.disconnect"}
        tarea = asyncio.create_task(escuchar())
        try:
            await self.app(scope, recibir, send)
        finally:
            tarea.cancel()
