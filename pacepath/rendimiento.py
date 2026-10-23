"""Rendimiento del motor de rutas (panel «Motor» de /admin).

Cada pedido que usa el motor (ruta, trazado, calles de Strava) mide sus etapas
y lo deja en la columna `motor` del registro, como JSON corto:

- espera: ms esperando el turno (el motor atiende de a uno: con 512 MB no caben
  dos cálculos a la vez) · cola: cuántos había esperando al llegar.
- mapa: ms en tener el grafo de la comuna · frio: 1 si hubo que cargarlo
  (descargar/leer del disco), 0 si ya estaba en memoria.
- calculo: ms de la búsqueda de la ruta · cpu: ms de CPU del pedido.
- mem: MB de memoria del proceso al terminar · dmem: cuánto creció en el pedido.
- km, error_pct, sem_km: la ruta entregada (largo total, desvío del largo
  pedido y semáforos por km), para ver la calidad junto al tiempo.
"""
from __future__ import annotations

import json
import os
import resource
import threading
import time
from contextlib import contextmanager

from raceengine_engine import context as _contextos

from . import comunas, uso

INICIO = time.time()
_lock = threading.Lock()
_esperando = 0


def memoria_mb() -> float:
    """Memoria residente del proceso ahora (Render gratis corta a 512 MB)."""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, IndexError):
        return maximo_mb()


def maximo_mb() -> float:
    """El máximo de memoria que alcanzó el proceso desde que partió."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024     # Linux: en KB


class Medicion:
    def __init__(self, comuna: str | None):
        self.comuna = comuna
        self.t0 = self._t = time.perf_counter()
        self.cpu0 = time.thread_time()
        self.mem0 = memoria_mb()
        self.datos: dict = {}

    def marca(self, etapa: str) -> None:
        """Lo que tardó `etapa` desde la marca anterior."""
        t = time.perf_counter()
        self.datos[etapa] = self.datos.get(etapa, 0) + round((t - self._t) * 1000)
        self._t = t

    def contexto(self):
        """El grafo de la comuna, anotando si estaba en memoria o hubo que cargarlo."""
        self.datos["frio"] = int(self.comuna not in _contextos._CONTEXTS)
        ctx = comunas.contexto(self.comuna)
        self.marca("mapa")
        return ctx

    def calidad(self, plan: dict) -> None:
        tot = plan.get("totales") or {}
        if tot.get("distancia_m"):
            self.datos["km"] = round(tot["distancia_m"] / 1000, 2)
        r = plan.get("recorrido") or plan.get("bloque")
        objetivo = (plan.get("sesion") or {}).get("distancia_m")
        if r and r.get("distancia_m"):
            if objetivo:
                self.datos["error_pct"] = round(100 * (r["distancia_m"] - objetivo) / objetivo, 1)
            self.datos["sem_km"] = round(r.get("semaforos", 0) / max(r["distancia_m"] / 1000, 0.1), 2)

    def cerrar(self) -> None:
        mem = memoria_mb()
        self.datos.update(cpu=round((time.thread_time() - self.cpu0) * 1000),
                          mem=round(mem), dmem=round(mem - self.mem0, 1))
        info = uso.consulta.get()
        if info is not None:
            info["motor"] = json.dumps({k: v for k, v in self.datos.items() if v is not None},
                                       separators=(",", ":"))


@contextmanager
def en_motor(lock: threading.Lock, comuna: str | None):
    """Toma el turno del motor midiendo la espera; al salir anota la medición."""
    global _esperando
    m = Medicion(comuna)
    with _lock:
        m.datos["cola"] = _esperando
        _esperando += 1
    try:
        lock.acquire()
    finally:
        with _lock:
            _esperando -= 1
    m.marca("espera")
    try:
        yield m
    finally:
        m.marca("calculo")           # también si no encontró ruta: ese tiempo igual se gastó
        lock.release()
        m.cerrar()


def estado() -> dict:
    """El proceso ahora: memoria, grafos en memoria, cola y hace cuánto partió."""
    mem = memoria_mb()
    return {"memoria_mb": round(mem), "maximo_mb": round(max(mem, maximo_mb())),
            "grafos": list(_contextos._CONTEXTS), "esperando": _esperando,
            "encendido_s": round(time.time() - INICIO), "cpus": os.cpu_count()}
