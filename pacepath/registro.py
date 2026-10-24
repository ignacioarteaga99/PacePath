"""Registro de consultas para el panel de administración (/admin).

Una fila por consulta a la API: cuándo, qué se pidió, si salió bien, cuánto
tardó y el error si lo hubo. El «usuario» es un identificador al azar guardado
en una cookie del teléfono: no está ligado a nombre, correo ni cuenta de Strava.
Nunca se guardan coordenadas, direcciones, IP ni datos de Strava.

Dónde se guarda:
  · DATABASE_URL (Postgres, p. ej. Neon) → persiste aunque Render duerma.
  · Sin ella, SQLite en /tmp: sirve en local, pero en Render se borra al dormir.

Las filas se escriben en segundo plano, de a lotes (con Postgres, cada 10
minutos: ver _Escritor), para no demorar las respuestas ni despertar a Neon a
cada rato; si la base no responde se reintenta en la vuelta siguiente.
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
            "detalle", "exito", "estado", "ms", "error", "idioma", "disp",
            # Consumo (panel «Consumo»): cuenta que consultó, bytes que llegaron y
            # que salieron (ya comprimidos) y llamadas a servicios externos.
            "cuenta", "bytes_in", "bytes_out", "externas",
            # Rendimiento del motor (panel «Motor»): JSON corto, ver rendimiento.py.
            "motor")
# Columnas agregadas después de crear las tablas (en Neon ya hay datos).
_MIGRACIONES = [("pp_eventos", "cuenta", "TEXT"), ("pp_eventos", "bytes_in", "INTEGER"),
                ("pp_eventos", "bytes_out", "INTEGER"), ("pp_eventos", "externas", "SMALLINT"),
                ("pp_eventos", "motor", "TEXT"),
                ("pp_usuarios", "idioma", "TEXT"),        # en qué idioma escribirle (es/en)
                # Para usar PacePath en otro aparato sin empezar de cero: la
                # conexión con Strava (cifrada, como la cookie) y las preferencias.
                ("pp_usuarios", "strava", "TEXT"), ("pp_usuarios", "datos", "TEXT"),
                ("pp_usuarios", "datos_ts", "TEXT")]

_DDL_PG = """CREATE TABLE IF NOT EXISTS pp_eventos (
  id BIGSERIAL PRIMARY KEY, ts TEXT NOT NULL, dia TEXT NOT NULL, usuario TEXT, strava SMALLINT,
  consulta TEXT, evento TEXT, tipo TEXT, comuna TEXT, detalle TEXT, exito SMALLINT,
  estado SMALLINT, ms INTEGER, error TEXT, idioma TEXT, disp TEXT,
  cuenta TEXT, bytes_in INTEGER, bytes_out INTEGER, externas SMALLINT, motor TEXT);
CREATE INDEX IF NOT EXISTS pp_eventos_dia ON pp_eventos (dia);
CREATE TABLE IF NOT EXISTS pp_ajustes (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pp_usuarios (
  id TEXT PRIMARY KEY, correo TEXT NOT NULL UNIQUE, nombre TEXT, clave TEXT,
  google TEXT UNIQUE, microsoft TEXT UNIQUE, verificado SMALLINT NOT NULL DEFAULT 0,
  beta SMALLINT NOT NULL DEFAULT 0, activo SMALLINT NOT NULL DEFAULT 1,
  creado TEXT NOT NULL, ultimo TEXT, idioma TEXT, strava TEXT, datos TEXT, datos_ts TEXT);
CREATE TABLE IF NOT EXISTS pp_sesiones (
  token TEXT PRIMARY KEY, usuario TEXT NOT NULL, creada TEXT NOT NULL, expira TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pp_sesiones_usuario ON pp_sesiones (usuario);
CREATE TABLE IF NOT EXISTS pp_tokens (
  token TEXT PRIMARY KEY, usuario TEXT NOT NULL, tipo TEXT NOT NULL, expira TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pp_zonas (
  id TEXT PRIMARY KEY, nombre TEXT, lat DOUBLE PRECISION NOT NULL, lon DOUBLE PRECISION NOT NULL,
  estado TEXT NOT NULL, pedida TEXT NOT NULL, enviada TEXT, lista TEXT, cuenta TEXT, avisar TEXT)"""
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
                    for tabla, col, tipo in _MIGRACIONES:
                        cur.execute(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {col} {tipo}")
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
                for tabla, col, tipo in _MIGRACIONES:
                    if col not in {f[1] for f in con.execute(f"PRAGMA table_info({tabla})")}:
                        con.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
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


    def ajustes(self) -> dict[str, str]:
        return dict(self._ejecutar("SELECT clave, valor FROM pp_ajustes", traer=True))

    def guardar_ajustes(self, valores: dict[str, str]) -> None:
        self._ejecutar("INSERT INTO pp_ajustes (clave, valor) VALUES (?, ?) "
                       "ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor", list(valores.items()))


class _Escritor:
    """Hilo que guarda las filas en la base, de a lotes.

    Con Postgres (Neon) se escribe cada `PACEPATH_REGISTRO_SEG` segundos (10 min
    por defecto) y no a cada consulta: Neon se duerme a los 5 min sin uso y el
    plan gratis da 100 horas de cómputo al mes; escribiendo a cada rato no se
    dormiría nunca (~180 h). Abrir el panel o apagar el servidor guardan al tiro.
    """

    def __init__(self, base: _Base):
        self.base = base
        self.intervalo = float(os.environ.get("PACEPATH_REGISTRO_SEG") or (600 if base.pg else 2))
        self.cola: queue.Queue[tuple] = queue.Queue()
        self.pendientes: list[tuple] = []
        self.ultimo_error: str | None = None
        self._ultima_poda = 0.0
        self._ahora = threading.Event()
        threading.Thread(target=self._bucle, name="registro", daemon=True).start()

    def _bucle(self) -> None:
        while True:
            self._ahora.wait(timeout=self.intervalo)
            self._ahora.clear()
            while True:
                try:
                    self.pendientes.append(self.cola.get_nowait())
                except queue.Empty:
                    break
            if not self.pendientes:
                continue
            try:
                for i in range(0, len(self.pendientes), 500):
                    self.base.insertar(self.pendientes[i:i + 500])
                for _ in self.pendientes:
                    self.cola.task_done()
                self.pendientes = []
                self.ultimo_error = None
                if time.time() - self._ultima_poda > 6 * 3600:
                    self._ultima_poda = time.time()
                    self.base.podar()
            except Exception as exc:  # noqa: BLE001 — la base caída no debe tumbar la app
                # Lo que no se pudo guardar se reintenta en la próxima vuelta.
                self.ultimo_error = f"{type(exc).__name__}: {exc}"[:300]
                print(f"[registro] no pude guardar {len(self.pendientes)} filas: {self.ultimo_error}", flush=True)
                if len(self.pendientes) > MAX_PENDIENTES:
                    for _ in self.pendientes[:-MAX_PENDIENTES]:
                        self.cola.task_done()
                    self.pendientes = self.pendientes[-MAX_PENDIENTES:]

    def anotar(self, fila: tuple) -> None:
        self.cola.put(fila)
        if self.cola.qsize() >= 2000:            # mucho tráfico: no acumular tanto en memoria
            self._ahora.set()

    def vaciar(self, plazo: float = 5.0) -> bool:
        """Guarda ya lo pendiente y espera a que termine (panel, apagado, tests)."""
        self._ahora.set()
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
    _el_escritor().anotar(tuple(_corto(fila.get(c)) for c in COLUMNAS))


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


# ── Ajustes del panel (p. ej. el acceso a la beta) ──────────────────────────
# En la misma base que el registro, porque en Render el disco se borra. Se
# escriben al tiro (no en lotes): son pocos y sólo cambian desde el panel.
def ajustes() -> dict[str, str]:
    return _el_escritor().base.ajustes()


def guardar_ajustes(valores: dict[str, str]) -> None:
    _el_escritor().base.guardar_ajustes(valores)


def sql(consulta: str, params: tuple = (), traer: bool = False):
    """Una consulta a la base (con «?»). Para cuentas.py: se ejecuta al tiro,
    no en lotes."""
    base = _el_escritor().base
    return base._ejecutar(consulta, params, traer=True) if traer else base._ejecutar(consulta, [params])


def tamanos() -> dict:
    """Tamaño de la base y de cada tabla (bytes) y cuántas filas tiene cada una:
    para ver cuánto falta para el límite del plan (Neon gratis: 0,5 GB)."""
    base = _el_escritor().base
    tablas = ("pp_eventos", "pp_usuarios", "pp_sesiones", "pp_tokens", "pp_ajustes")
    filas = {t: base._ejecutar(f"SELECT COUNT(*) FROM {t}", (), traer=True)[0][0] for t in tablas}
    if base.pg:
        total = base._ejecutar("SELECT pg_database_size(current_database())", (), traer=True)[0][0]
        por_tabla = {t: base._ejecutar("SELECT pg_total_relation_size(?)", (t,), traer=True)[0][0] for t in tablas}
    else:
        ruta = base.url or os.path.join(tempfile.gettempdir(), "pacepath_registro.sqlite")
        total = os.path.getsize(ruta) if os.path.exists(ruta) else 0
        por_tabla = {t: None for t in tablas}           # SQLite no lo da por tabla
    return {"motor": base.nombre, "total_bytes": total,
            "tablas": [{"tabla": t, "filas": filas[t], "bytes": por_tabla[t]} for t in tablas]}
