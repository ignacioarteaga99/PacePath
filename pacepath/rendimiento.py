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

Y en vivo (tarjeta «En vivo», sólo en memoria): cada segundo se toma una
muestra del proceso (memoria, CPU, pedidos en curso y en cola, datos que
salen) y cada pedido en curso dice en qué etapa va, con un avance estimado
según lo que tardaron los últimos pedidos parecidos.
"""
from __future__ import annotations

import ctypes
import itertools
import json
import os
import resource
import statistics
import threading
import time
from collections import deque
from contextlib import contextmanager

from raceengine_engine import context as _contextos

from . import cancelar, comunas, uso

INICIO = time.time()

# glibc no devuelve al sistema la memoria que Python suelta (queda «reservada»
# y la RSS no baja): después de cada ruta se le pide que la devuelva. Cuesta
# 1-2 ms y baja ~40 MB con Las Condes. Junto con MALLOC_ARENA_MAX=2 (Dockerfile)
# la memoria tras cambiar de comunas quedó en ~195 MB en vez de ~360 MB.
try:
    _malloc_trim = ctypes.CDLL("libc.so.6").malloc_trim
except (OSError, AttributeError):          # sin glibc (macOS, Alpine): no hace falta
    _malloc_trim = None


def devolver_memoria() -> None:
    if _malloc_trim is not None:
        _malloc_trim(0)
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
    def __init__(self, comuna: str | None, clave: str):
        self.comuna = comuna
        self.clave = clave
        info = uso.consulta.get()
        self.vivo: dict | None = info.get("_vivo") if info else None
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
        etapa(self.vivo, "cargando mapa" if self.datos["frio"] else "preparando", f"mapa:{self.comuna}"
              if self.datos["frio"] else None)
        ctx = comunas.contexto(self.comuna)
        self.marca("mapa")
        cancelar.revisar()
        etapa(self.vivo, "calculando", f"calculo:{self.clave}")
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
        if self.datos.get("frio") and "mapa" in self.datos:
            _tipico(f"mapa:{self.comuna}", self.datos["mapa"])
        if "calculo" in self.datos:
            _tipico(f"calculo:{self.clave}", self.datos["calculo"])
        etapa(self.vivo, "terminando")
        mem = memoria_mb()
        self.datos.update(cpu=round((time.thread_time() - self.cpu0) * 1000),
                          mem=round(mem), dmem=round(mem - self.mem0, 1))
        info = uso.consulta.get()
        if info is not None:
            info["motor"] = json.dumps({k: v for k, v in self.datos.items() if v is not None},
                                       separators=(",", ":"))


@contextmanager
def en_motor(lock: threading.Lock, comuna: str | None, que: str | None = None, clave: str | None = None):
    """Toma el turno del motor midiendo la espera; al salir anota la medición.
    `que` (p. ej. «6x1k») y `clave` (tipo de sesión) son para la vista en vivo:
    qué se calcula y con qué pedidos anteriores comparar el avance."""
    global _esperando
    m = Medicion(comuna, clave or (uso.consulta.get() or {}).get("consulta", "motor"))
    if m.vivo is not None:
        m.vivo.update(comuna=comuna, que=que)
    with _lock:
        m.datos["cola"] = _esperando
        _esperando += 1
    etapa(m.vivo, "esperando turno")
    if m.vivo is not None:
        m.vivo["cola"] = m.datos["cola"]
    try:
        lock.acquire()
    finally:
        with _lock:
            _esperando -= 1
    m.marca("espera")
    try:
        cancelar.revisar()           # canceló mientras esperaba su turno: ni empezar
        yield m
    finally:
        m.marca("calculo")           # también si no encontró ruta: ese tiempo igual se gastó
        lock.release()
        devolver_memoria()
        m.cerrar()


def estado() -> dict:
    """El proceso ahora: memoria, grafos en memoria, cola y hace cuánto partió."""
    mem = memoria_mb()
    return {"memoria_mb": round(mem), "maximo_mb": round(max(mem, maximo_mb())),
            "grafos": list(_contextos._CONTEXTS), "esperando": _esperando,
            "encendido_s": round(time.time() - INICIO), "cpus": os.cpu_count()}


# ── En vivo ─────────────────────────────────────────────────────────────────
MUESTRAS = 600                     # 10 min, una por segundo
_activos: dict[int, dict] = {}
_recientes: deque = deque(maxlen=15)
_serie: deque = deque(maxlen=MUESTRAS)
_tipicos: dict[str, deque] = {}
_bytes_salida = 0
_ids = itertools.count(1)


def empezar(info: dict) -> dict:
    """Un pedido que entra (lo llama el middleware que registra consultas)."""
    ahora = time.time()
    p = {"id": next(_ids), "consulta": info.get("consulta"), "cuenta": info.get("cuenta"),
         "usuario": info.get("usuario"), "disp": info.get("disp"), "t0": ahora,
         "etapa": "procesando", "t_etapa": ahora, "tipico": None}
    with _lock:
        _activos[p["id"]] = p
    info["_vivo"] = p
    return p


def etapa(p: dict | None, nombre: str, tipico: str | None = None) -> None:
    if p is not None:
        p.update(etapa=nombre, t_etapa=time.time(), tipico=tipico)


def terminar(p: dict, info: dict) -> None:
    global _bytes_salida
    with _lock:
        _activos.pop(p["id"], None)
        _bytes_salida += (info.get("bytes_out") or 0) + (info.get("bytes_in") or 0)
        _recientes.appendleft({"consulta": p["consulta"], "que": info.get("detalle") or p.get("que"),
                               "comuna": info.get("comuna") or p.get("comuna"), "cuenta": p["cuenta"],
                               "usuario": p["usuario"], "ms": info.get("ms"), "estado": info.get("estado"),
                               "exito": bool(info.get("exito")), "fin": round(time.time(), 1),
                               "motor": json.loads(info["motor"]) if info.get("motor") else None})


def _tipico(clave: str, ms: float) -> None:
    with _lock:
        _tipicos.setdefault(clave, deque(maxlen=50)).append(ms)


def _mediana(clave: str | None) -> float | None:
    xs = _tipicos.get(clave) if clave else None
    return statistics.median(xs) if xs else None


def _muestrear() -> None:
    t_ant, cpu_ant, bytes_ant = time.time(), sum(os.times()[:2]), 0
    while True:
        time.sleep(1)
        t, cpu = time.time(), sum(os.times()[:2])
        with _lock:
            b = _bytes_salida
            activos = len(_activos)
        dt = max(t - t_ant, 1e-3)
        _serie.append((round(t, 1), round(memoria_mb()), round(100 * (cpu - cpu_ant) / dt),
                       activos, _esperando, round((b - bytes_ant) / dt)))
        t_ant, cpu_ant, bytes_ant = t, cpu, b


threading.Thread(target=_muestrear, name="rendimiento", daemon=True).start()


def vivo(desde: float = 0) -> dict:
    """Para la tarjeta «En vivo»: las muestras nuevas desde `desde`, los
    pedidos en curso (etapa, cuánto llevan y avance estimado) y los recientes."""
    ahora = time.time()
    with _lock:
        activos = [dict(p) for p in _activos.values()]
        recientes = list(_recientes)
    en_curso = []
    for p in sorted(activos, key=lambda p: p["t0"]):
        en_etapa = (ahora - p["t_etapa"]) * 1000
        tipico = _mediana(p["tipico"])
        en_curso.append({"consulta": p["consulta"], "que": p.get("que"), "comuna": p.get("comuna"),
                         "cuenta": p["cuenta"], "usuario": p["usuario"], "disp": p["disp"],
                         "etapa": p["etapa"], "cola": p.get("cola"),
                         "lleva_ms": round((ahora - p["t0"]) * 1000), "en_etapa_ms": round(en_etapa),
                         "tipico_ms": round(tipico) if tipico else None,
                         "avance": min(0.95, en_etapa / tipico) if tipico else None})
    return {"ahora": estado(), "t": round(ahora, 1),
            "serie": [m for m in _serie if m[0] > desde],
            "en_curso": en_curso, "recientes": recientes}
