"""Beta cerrada: si está activa, sólo entran las cuentas habilitadas para la beta.

Se activa o desactiva desde el panel (/admin); quién tiene beta se marca en
las cuentas (cuentas.py). Por defecto está activa.

Dónde se guarda: en la tabla de ajustes de la base del registro (Neon), porque
en Render el disco se borra al dormir. Se lee una vez y queda en memoria (el
servidor es un solo proceso): las visitas no despiertan a Neon.
"""
from __future__ import annotations

import threading
import time

from . import registro

_REINTENTO = 60                             # si la base no responde, volver a leerla en 1 min
_estado: dict[str, str] | None = None
_reintentar = 0.0                           # si la base falló: cuándo volver a leerla
_lock = threading.Lock()


def _actual() -> dict[str, str]:
    global _estado, _reintentar
    with _lock:
        if _estado is None or (_reintentar and time.time() > _reintentar):
            try:
                guardado = registro.ajustes()
                _reintentar = 0.0
            except Exception as exc:  # noqa: BLE001 — sin base: activa, y reintentar luego
                print(f"[acceso] no pude leer los ajustes: {type(exc).__name__}: {exc}", flush=True)
                guardado, _reintentar = {}, time.time() + _REINTENTO
            _estado = {"activo": guardado.get("beta_activo", "1")}
        return _estado


def olvidar() -> None:
    """Vuelve a leer la base en la próxima consulta (tests)."""
    global _estado, _reintentar
    with _lock:
        _estado, _reintentar = None, 0.0


def activo() -> bool:
    return _actual()["activo"] == "1"


def estado() -> dict:
    return {"activo": activo()}


def configurar(activo: bool | None = None) -> dict:
    """Activa o desactiva la beta cerrada y lo guarda en la base (al tiro)."""
    global _estado
    if activo is not None:
        registro.guardar_ajustes({"beta_activo": "1" if activo else "0"})
        with _lock:
            _estado = {"activo": "1" if activo else "0"}
    return estado()
