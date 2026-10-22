"""Registro de consultas para el panel de administración (/admin).

Una fila por consulta a la API: cuándo, qué se pidió, si salió bien, cuánto
tardó y el error si lo hubo. El «usuario» es un identificador al azar guardado
en una cookie del teléfono: no está ligado a nombre, correo ni cuenta de Strava.
Nunca se guardan coordenadas, direcciones, IP ni datos de Strava.

Dónde se guarda:
  · DATABASE_URL (Postgres, p. ej. Neon) → persiste aunque Render duerma.
  · Sin ella, SQLite en /tmp: sirve en local, pero en Render se borra al dormir.

Las filas se escriben en segundo plano, de a lotes, para no demorar las
respuestas; si la base no responde se reintenta y, como último recurso, la
fila queda en el log del servidor.
"""
from __future__ import annotations

import os
import queue
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ZONA = ZoneInfo("America/Santiago")
DIAS_GUARDADOS = 365
MAX_PENDIENTES = 20_000            # si la base no responde por mucho rato, no crecer sin fin
MAX_FILAS_CONSULTA = 100_000

COLUMNAS = ("ts", "dia", "usuario", "strava", "consulta", "evento", "tipo", "comuna",
            "detalle", "exito", "estado", "ms", "error", "idioma", "disp")

_DDL_PG = """CREATE TABLE IF NOT EXISTS pp_eventos (
  id BIGSERIAL PRIMARY KEY, ts TEXT NOT NULL, dia TEXT NOT NULL, usuario TEXT, strava SMALLINT,
  consulta TEXT, evento TEXT, tipo TEXT, comuna TEXT, detalle TEXT, exito SMALLINT,
  estado SMALLINT, ms INTEGER, error TEXT, idioma TEXT, disp TEXT);
CREATE INDEX IF NOT EXISTS pp_eventos_dia ON pp_eventos (dia)"""
_DDL_SQLITE = _DDL_PG.replace("BIGSERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")


def ahora() -> tuple[str, str]:
    """(instante UTC ISO, día en Santiago): los filtros del panel van por día local."""
    t = datetime.now(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ"), t.astimezone(ZONA).strftime("%Y-%m-%d")


def hoy() -> str:
    return ahora()[1]


class _Base:
    """Postgres o SQLite con la misma interfaz (SQL con «?»)."""

    def __init__(self, url: str | None):
        self.url = url
        self.pg = bool(url and url.startswith(("postgres://", "postgresql://")))
        self._sqlite: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._lista = False

    @property
    def nombre(self) -> str:
        return "postgres" if self.pg else "sqlite"

    def _con(self):
        if self.pg:
            import psycopg  # sólo hace falta con DATABASE_URL
            return psycopg.connect(self.url, autocommit=True, connect_timeout=10)
        if self._sqlite is None:
            ruta = self.url or os.path.join(tempfile.gettempdir(), "pacepath_registro.sqlite")
            self._sqlite = sqlite3.connect(ruta, check_same_thread=False, isolation_level=None)
        return self._sqlite

    def _ejecutar(self, sql: str, filas=None, traer: bool = False):
        if self.pg:
            sql = sql.replace("?", "%s")
            with self._con() as con, con.cursor() as cur:
                if not self._lista:
                    for parte in _DDL_PG.split(";"):
                        cur.execute(parte)
                    self._lista = True
                if filas is not None and not traer:
                    cur.executemany(sql, filas)
                else:
                    cur.execute(sql, filas or ())
                return cur.fetchall() if traer else None
        with self._lock:
            con = self._con()
            if not self._lista:
                con.executescript(_DDL_SQLITE)
                self._lista = True
            if filas is not None and not traer:
                con.executemany(sql, filas)
                return None
            return con.execute(sql, filas or ()).fetchall()

    def insertar(self, filas: list[tuple]) -> None:
        marcas = ",".join("?" * len(COLUMNAS))
        self._ejecutar(f"INSERT INTO pp_eventos ({','.join(COLUMNAS)}) VALUES ({marcas})", filas)

    def podar(self) -> None:
        limite = (datetime.now(ZONA) - timedelta(days=DIAS_GUARDADOS)).strftime("%Y-%m-%d")
        self._ejecutar("DELETE FROM pp_eventos WHERE dia < ?", [(limite,)])

    def consultar(self, desde: str, hasta: str, limite: int) -> list[tuple]:
        return self._ejecutar(f"SELECT {','.join(COLUMNAS)} FROM pp_eventos WHERE dia >= ? AND dia <= ? "
                              "ORDER BY ts DESC, id DESC LIMIT ?", (desde, hasta, limite), traer=True)


class _Escritor:
    """Hilo que vacía la cola de filas en la base, de a lotes."""

    def __init__(self, base: _Base):
        self.base = base
        self.cola: queue.Queue[tuple] = queue.Queue()
        self.pendientes: list[tuple] = []
        self.ultimo_error: str | None = None
        self._ultima_poda = 0.0
        threading.Thread(target=self._bucle, name="registro", daemon=True).start()

    def _bucle(self) -> None:
        espera = 1.0
        while True:
            try:
                self.pendientes.append(self.cola.get(timeout=2))
            except queue.Empty:
                if not self.pendientes:
                    continue
            while len(self.pendientes) < 500:
                try:
                    self.pendientes.append(self.cola.get_nowait())
                except queue.Empty:
                    break
            try:
                self.base.insertar(self.pendientes)
                for _ in self.pendientes:
                    self.cola.task_done()
                self.pendientes = []
                self.ultimo_error = None
                espera = 1.0
                if time.time() - self._ultima_poda > 6 * 3600:
                    self._ultima_poda = time.time()
                    self.base.podar()
            except Exception as exc:  # noqa: BLE001 — la base caída no debe tumbar la app
                self.ultimo_error = f"{type(exc).__name__}: {exc}"[:300]
                print(f"[registro] no pude guardar {len(self.pendientes)} filas: {self.ultimo_error}", flush=True)
                if len(self.pendientes) > MAX_PENDIENTES:
                    for _ in self.pendientes[:-MAX_PENDIENTES]:
                        self.cola.task_done()
                    self.pendientes = self.pendientes[-MAX_PENDIENTES:]
                time.sleep(espera)
                espera = min(espera * 2, 60)

    def vaciar(self, plazo: float = 5.0) -> bool:
        """Espera a que todo lo anotado esté guardado (tests y apagado)."""
        fin = time.time() + plazo
        while time.time() < fin:
            if self.cola.unfinished_tasks == 0:
                return True
            time.sleep(0.05)
        return False


_escritor: _Escritor | None = None
_lock_escritor = threading.RLock()       # _el_escritor() llama a configurar() con él tomado


def configurar(url: str | None = None) -> None:
    """Elige la base (por defecto DATABASE_URL). Los tests la cambian por una SQLite temporal."""
    global _escritor
    with _lock_escritor:
        _escritor = _Escritor(_Base(url if url is not None else os.environ.get("DATABASE_URL")))


def _el_escritor() -> _Escritor:
    if _escritor is None:
        with _lock_escritor:
            if _escritor is None:
                configurar()
    return _escritor  # type: ignore[return-value]


def anotar(fila: dict) -> None:
    ts, dia = ahora()
    fila = {"ts": ts, "dia": dia} | fila
    _el_escritor().cola.put(tuple(_corto(fila.get(c)) for c in COLUMNAS))


def _corto(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        return v[:300]
    return v


def vaciar(plazo: float = 5.0) -> bool:
    return _escritor.vaciar(plazo) if _escritor else True


def consultar(desde: str, hasta: str) -> dict:
    esc = _el_escritor()
    vaciar(2.0)
    filas = esc.base.consultar(desde, hasta, MAX_FILAS_CONSULTA + 1)
    return {"columnas": list(COLUMNAS), "filas": [list(f) for f in filas[:MAX_FILAS_CONSULTA]],
            "truncado": len(filas) > MAX_FILAS_CONSULTA, "almacen": esc.base.nombre,
            "error_almacen": esc.ultimo_error, "hoy": hoy()}
