"""Beta cerrada: la app pide un usuario y una clave antes de entrar.

Se activa o desactiva desde el panel (/admin), donde también se cambian el
usuario y la clave. Por defecto está activa, con el usuario «tester» y la clave
«admin123».

Dónde se guarda: en la tabla de ajustes de la base del registro (Neon), porque
en Render el disco se borra al dormir. Se lee una vez y queda en memoria (el
servidor es un solo proceso): las visitas no despiertan a Neon. La clave se
guarda con hash (PBKDF2), nunca tal cual.

Quien entra recibe una cookie que vale mientras no cambien el usuario ni la
clave: cambiarlos desde el panel cierra la sesión de todos los testers.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from . import registro

COOKIE = "pp_beta"
DURACION = 90 * 24 * 3600                   # la cookie dura 90 días
USUARIO_INICIAL = "tester"
CLAVE_INICIAL = "admin123"
_ITERACIONES = 200_000
_REINTENTO = 60                             # si la base no responde, volver a leerla en 1 min


def hash_clave(clave: str, sal: str | None = None) -> str:
    sal = sal or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", clave.encode(), sal.encode(), _ITERACIONES).hex()
    return f"pbkdf2${sal}${h}"


def _clave_ok(clave: str, guardada: str) -> bool:
    try:
        _, sal, _h = guardada.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_clave(clave, sal), guardada)


# Sal fija para la clave inicial: así su cookie no cambia al reiniciar el
# servidor cuando todavía no se ha guardado nada en la base.
_INICIAL = {"activo": "1", "usuario": USUARIO_INICIAL, "clave": hash_clave(CLAVE_INICIAL, "pacepath-inicial")}
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
            except Exception as exc:  # noqa: BLE001 — sin base: lo inicial, y reintentar luego
                print(f"[acceso] no pude leer los ajustes: {type(exc).__name__}: {exc}", flush=True)
                guardado, _reintentar = {}, time.time() + _REINTENTO
            _estado = _INICIAL | {k[len("beta_"):]: v for k, v in guardado.items() if k.startswith("beta_")}
        return _estado


def olvidar() -> None:
    """Vuelve a leer la base en la próxima consulta (tests)."""
    global _estado, _reintentar
    with _lock:
        _estado, _reintentar = None, 0.0


def activo() -> bool:
    return _actual()["activo"] == "1"


def estado() -> dict:
    """Lo que ve el panel (sin la clave)."""
    e = _actual()
    return {"activo": e["activo"] == "1", "usuario": e["usuario"],
            "clave_inicial": e["clave"] == _INICIAL["clave"]}


def ficha() -> str:
    """El valor de la cookie: cambia si cambian el usuario o la clave."""
    e = _actual()
    return hashlib.sha256(f"pp-beta|{e['usuario']}|{e['clave']}".encode()).hexdigest()[:32]


def cookie_valida(valor: str | None) -> bool:
    return bool(valor) and hmac.compare_digest(valor.encode(), ficha().encode())


def entrar(usuario: str, clave: str) -> bool:
    e = _actual()
    usuario_ok = hmac.compare_digest(usuario.strip().lower().encode(), e["usuario"].lower().encode())
    return _clave_ok(clave, e["clave"]) and usuario_ok


def configurar(activo: bool | None = None, usuario: str | None = None, clave: str | None = None) -> dict:
    """Cambia los ajustes desde el panel y los guarda en la base (al tiro)."""
    global _estado
    nuevo = dict(_actual())
    if activo is not None:
        nuevo["activo"] = "1" if activo else "0"
    if usuario:
        nuevo["usuario"] = usuario.strip()
    if clave:
        nuevo["clave"] = hash_clave(clave)
    registro.guardar_ajustes({f"beta_{k}": v for k, v in nuevo.items()})
    with _lock:
        _estado = nuevo
    return estado()
