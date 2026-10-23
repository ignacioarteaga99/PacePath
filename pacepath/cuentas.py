"""Cuentas de usuario: registro con correo y clave, o con Google o Microsoft.

Las cuentas viven en la base (Neon), tablas `pp_usuarios` y `pp_sesiones`.
La sesión es una cookie con un token al azar; en la base se guarda sólo su
hash (si alguien lee la base, no puede entrar con eso). Las claves, con scrypt.

Vincular por correo (quien se registró con correo y después entra con Google):
  · Google: sólo si Google dice que el correo está verificado. Si la cuenta
    tenía clave y su correo nunca se verificó, la clave se borra: si no, quien
    registró antes ese correo ajeno podría seguir entrando.
  · Microsoft: sólo con cuentas personales (outlook, hotmail…), cuyo correo
    Microsoft verifica. En las de empresa el correo lo escribe el
    administrador de esa empresa y podría ser cualquiera: se vinculan sólo por
    su identificador de Microsoft, nunca por correo.

La beta (acceso.py) deja entrar sólo a las cuentas marcadas como beta.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from . import registro
from .idioma import actual as idioma_actual

COOKIE = "pp_sesion"
DURACION = 90 * 24 * 3600
CLAVE_MIN = 8
_CACHE_S = 300                       # una sesión leída de la base vale 5 min en memoria
CAMPOS = ("id", "correo", "nombre", "clave", "google", "microsoft", "verificado", "beta",
          "activo", "creado", "ultimo", "idioma")


class ErrorCuenta(ValueError):
    """Algo que se le explica al usuario (correo repetido, clave corta…)."""


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalizar_correo(correo: str) -> str:
    c = (correo or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", c) or len(c) > 200:
        raise ErrorCuenta("correo_invalido")
    return c


# ── Claves ──────────────────────────────────────────────────────────────────

def hash_clave(clave: str) -> str:
    sal = secrets.token_bytes(16)
    h = hashlib.scrypt(clave.encode(), salt=sal, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${sal.hex()}${h.hex()}"


def clave_ok(clave: str, guardada: str | None) -> bool:
    try:
        _, sal, h = (guardada or "").split("$")
        calc = hashlib.scrypt(clave.encode(), salt=bytes.fromhex(sal), n=2 ** 14, r=8, p=1, dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), h)


# ── Usuarios ────────────────────────────────────────────────────────────────

def _fila(f) -> dict | None:
    if not f:
        return None
    u = dict(zip(CAMPOS, f))
    for k in ("verificado", "beta", "activo"):
        u[k] = bool(u[k])
    return u


def _uno(donde: str, valor) -> dict | None:
    filas = registro.sql(f"SELECT {','.join(CAMPOS)} FROM pp_usuarios WHERE {donde} = ?", (valor,), traer=True)
    return _fila(filas[0]) if filas else None


def por_id(uid: str) -> dict | None:
    return _uno("id", uid)


def por_correo(correo: str) -> dict | None:
    return _uno("correo", correo)


def publico(u: dict) -> dict:
    """Lo que se puede mostrar (sin la clave)."""
    return {"id": u["id"], "correo": u["correo"], "nombre": u["nombre"], "beta": u["beta"],
            "activo": u["activo"], "verificado": u["verificado"], "creado": u["creado"],
            "ultimo": u["ultimo"], "metodos": [m for m in ("clave", "google", "microsoft") if u.get(m)]}


def listar() -> list[dict]:
    filas = registro.sql(f"SELECT {','.join(CAMPOS)} FROM pp_usuarios ORDER BY creado DESC", (), traer=True)
    return [publico(_fila(f)) for f in filas]


def crear(correo: str, nombre: str | None = None, clave: str | None = None, beta: bool = False,
          google: str | None = None, microsoft: str | None = None, verificado: bool = False,
          idioma: str | None = None) -> dict:
    correo = normalizar_correo(correo)
    if por_correo(correo):
        raise ErrorCuenta("correo_existe")
    if clave is not None and len(clave) < CLAVE_MIN:
        raise ErrorCuenta("clave_corta")
    uid = secrets.token_hex(8)
    registro.sql("INSERT INTO pp_usuarios (id, correo, nombre, clave, google, microsoft, verificado, beta, activo, creado, "
                 "idioma) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                 (uid, correo, (nombre or "").strip()[:80] or None, hash_clave(clave) if clave else None,
                  google, microsoft, int(verificado), int(beta), _ahora(), idioma))
    return por_id(uid)


def actualizar(uid: str, **cambios) -> dict:
    """Cambia nombre, correo, beta, activo o clave (los que vengan)."""
    u = por_id(uid)
    if not u:
        raise ErrorCuenta("no_existe")
    sets, vals = [], []
    if cambios.get("correo") is not None:
        correo = normalizar_correo(cambios["correo"])
        otro = por_correo(correo)
        if otro and otro["id"] != uid:
            raise ErrorCuenta("correo_existe")
        sets.append("correo = ?"); vals.append(correo)
    if cambios.get("nombre") is not None:
        sets.append("nombre = ?"); vals.append(cambios["nombre"].strip()[:80] or None)
    for k in ("beta", "activo", "verificado"):
        if cambios.get(k) is not None:
            sets.append(f"{k} = ?"); vals.append(int(bool(cambios[k])))
    if cambios.get("clave") is not None:
        if len(cambios["clave"]) < CLAVE_MIN:
            raise ErrorCuenta("clave_corta")
        sets.append("clave = ?"); vals.append(hash_clave(cambios["clave"]))
    for k in ("google", "microsoft", "idioma"):
        if k in cambios:
            sets.append(f"{k} = ?"); vals.append(cambios[k])
    if cambios.get("quitar_clave"):
        sets.append("clave = ?"); vals.append(None)
    if sets:
        registro.sql(f"UPDATE pp_usuarios SET {', '.join(sets)} WHERE id = ?", (*vals, uid))
    if cambios.get("activo") is False or cambios.get("clave") is not None:
        cerrar_sesiones(uid)                   # bloqueado o clave nueva: fuera de todos lados
    _olvidar_usuario(uid)
    return por_id(uid)


def borrar(uid: str) -> None:
    cerrar_sesiones(uid)
    registro.sql("DELETE FROM pp_usuarios WHERE id = ?", (uid,))
    registro.sql("DELETE FROM pp_tokens WHERE usuario = ?", (uid,))
    registro.vaciar()
    registro.sql("UPDATE pp_eventos SET cuenta = NULL WHERE cuenta = ?", (uid,))   # el registro deja de ser suyo
    _olvidar_usuario(uid)


# ── Entrar ──────────────────────────────────────────────────────────────────

_intentos: dict[str, list[float]] = {}
_lock = threading.Lock()
MAX_INTENTOS, VENTANA_S = 6, 600


def _frenar(correo: str) -> None:
    with _lock:
        recientes = [t for t in _intentos.get(correo, []) if time.time() - t < VENTANA_S]
        _intentos[correo] = recientes
        if len(recientes) >= MAX_INTENTOS:
            raise ErrorCuenta("demasiados_intentos")


def _fallo(correo: str) -> None:
    with _lock:
        _intentos.setdefault(correo, []).append(time.time())


def entrar_con_clave(correo: str, clave: str) -> dict:
    correo = normalizar_correo(correo)
    _frenar(correo)
    u = por_correo(correo)
    if not u or not clave_ok(clave, u["clave"]):
        _fallo(correo)
        raise ErrorCuenta("clave_incorrecta")
    if not u["activo"]:
        raise ErrorCuenta("bloqueada")
    return u


TENANT_PERSONAL = "9188040d-6c67-4c5b-b112-36a304b66dad"   # cuentas personales de Microsoft


def entrar_con_proveedor(proveedor: str, sub: str, correo: str | None, nombre: str | None,
                         correo_confiable: bool) -> dict:
    """Busca o crea la cuenta de quien vuelve de Google o Microsoft."""
    u = _uno(proveedor, sub)
    if u is None and correo:
        correo = normalizar_correo(correo)
        existente = por_correo(correo)
        if existente:
            if not correo_confiable:
                raise ErrorCuenta("correo_existe_otro_metodo")
            cambios = {proveedor: sub, "verificado": True}
            if existente["clave"] and not existente["verificado"]:
                cambios["quitar_clave"] = True     # ver la nota del módulo
            u = actualizar(existente["id"], **cambios)
        else:
            u = crear(correo, nombre, google=sub if proveedor == "google" else None,
                      microsoft=sub if proveedor == "microsoft" else None, verificado=correo_confiable,
                      idioma=idioma_actual())
            u["nueva"] = True              # para la bienvenida (no se guarda)
    if u is None:
        raise ErrorCuenta("sin_correo")
    if not u["activo"]:
        raise ErrorCuenta("bloqueada")
    if nombre and not u["nombre"]:
        u = actualizar(u["id"], nombre=nombre) | {"nueva": u.get("nueva", False)}
    return u


# ── Sesiones ────────────────────────────────────────────────────────────────

_sesiones: dict[str, tuple[float, dict | None]] = {}


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def abrir_sesion(u: dict) -> str:
    token = secrets.token_urlsafe(32)
    expira = (datetime.now(timezone.utc) + timedelta(seconds=DURACION)).strftime("%Y-%m-%dT%H:%M:%SZ")
    registro.sql("INSERT INTO pp_sesiones (token, usuario, creada, expira) VALUES (?, ?, ?, ?)",
                 (_h(token), u["id"], _ahora(), expira))
    registro.sql("UPDATE pp_usuarios SET ultimo = ? WHERE id = ?", (_ahora(), u["id"]))
    return token


def de_cookie(token: str | None) -> dict | None:
    """El usuario de la cookie de sesión (o None). Se recuerda 5 minutos para
    no consultar la base a cada pedido (y no despertar a Neon)."""
    if not token or len(token) > 100:
        return None
    clave = _h(token)
    with _lock:
        hit = _sesiones.get(clave)
    if hit and time.time() - hit[0] < _CACHE_S:
        return hit[1]
    filas = registro.sql("SELECT usuario, expira FROM pp_sesiones WHERE token = ?", (clave,), traer=True)
    u = None
    if filas and filas[0][1] > _ahora():
        u = por_id(filas[0][0])
        if u and not u["activo"]:
            u = None
    with _lock:
        if len(_sesiones) > 5000:
            _sesiones.clear()
        _sesiones[clave] = (time.time(), u)
    return u


def cerrar_sesion(token: str | None) -> None:
    if token:
        registro.sql("DELETE FROM pp_sesiones WHERE token = ?", (_h(token),))
        with _lock:
            _sesiones.pop(_h(token), None)


def cerrar_sesiones(uid: str) -> None:
    registro.sql("DELETE FROM pp_sesiones WHERE usuario = ?", (uid,))
    _olvidar_usuario(uid)


def _olvidar_usuario(uid: str) -> None:
    with _lock:
        for k in [k for k, (_, u) in _sesiones.items() if u and u["id"] == uid]:
            del _sesiones[k]


# ── Códigos de un solo uso: verificar el correo y recuperar la clave ─────────
# Van en un enlace por correo (correo.py). En la base, sólo su hash.
HORAS = {"verificar": 48, "recuperar": 1}


def nuevo_codigo(uid: str, tipo: str) -> str:
    codigo = secrets.token_urlsafe(32)
    expira = (datetime.now(timezone.utc) + timedelta(hours=HORAS[tipo])).strftime("%Y-%m-%dT%H:%M:%SZ")
    registro.sql("DELETE FROM pp_tokens WHERE usuario = ? AND tipo = ?", (uid, tipo))   # sólo el último vale
    registro.sql("INSERT INTO pp_tokens (token, usuario, tipo, expira) VALUES (?, ?, ?, ?)",
                 (_h(codigo), uid, tipo, expira))
    return codigo


def usar_codigo(codigo: str, tipo: str) -> dict:
    """El usuario del código (y lo gasta). Vencido, usado o inventado: error."""
    filas = registro.sql("SELECT usuario, expira FROM pp_tokens WHERE token = ? AND tipo = ?",
                         (_h(codigo or ""), tipo), traer=True)
    registro.sql("DELETE FROM pp_tokens WHERE token = ?", (_h(codigo or ""),))
    if not filas or filas[0][1] < _ahora():
        raise ErrorCuenta("codigo_invalido")
    u = por_id(filas[0][0])
    if not u or not u["activo"]:
        raise ErrorCuenta("codigo_invalido")
    return u


def falta_verificar(u: dict, correo_activo: bool) -> bool:
    """Con el envío de correos encendido, quien se registró con correo y clave
    debe confirmar su correo antes de entrar (Google y Microsoft ya lo hacen)."""
    return correo_activo and not u["verificado"] and not (u.get("google") or u.get("microsoft"))
