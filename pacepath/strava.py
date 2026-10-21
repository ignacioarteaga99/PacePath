"""Conexión con Strava: OAuth propio de PacePath y lectura de carreras.

Sin base de datos (por ahora):
  · El acceso del usuario (tokens) viaja en una cookie CIFRADA (Fernet) que sólo
    el servidor puede leer. La clave sale de STRAVA_CLIENT_SECRET (o de
    PACEPATH_SECRET si existe), que vive en Render y nunca en el repositorio.
  · Las carreras se guardan en el navegador del propio usuario.

Así se cumplen los términos de la API de Strava sin montar nada más: los datos
de cada usuario sólo los ve ese usuario, y al desconectar se revoca el acceso
en Strava y se borra todo (cookie y datos del navegador).

Flujo:
  /api/strava/conectar → strava.com/oauth/authorize → /api/strava/vuelta
  (cambia el código por tokens) → cookie → la app pide /api/strava/actividades.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

from cryptography.fernet import Fernet, InvalidToken

from .idioma import tr

AUTORIZAR = "https://www.strava.com/oauth/authorize"
TOKEN = "https://www.strava.com/oauth/token"
REVOCAR = "https://www.strava.com/oauth/deauthorize"
API = "https://www.strava.com/api/v3"
ALCANCE = "read,activity:read_all"
COOKIE = "pp_strava"
COOKIE_ESTADO = "pp_strava_estado"
# Tipos de actividad que cuentan como «correr por la calle». Las virtuales
# (cinta, Zwift) no pasan por ninguna calle.
CARRERA = {"Run", "TrailRun"}


class StravaError(Exception):
    pass


def client_id() -> str:
    return os.environ.get("STRAVA_CLIENT_ID", "")


def _secreto() -> str:
    return os.environ.get("PACEPATH_SECRET") or os.environ.get("STRAVA_CLIENT_SECRET", "")


def configurado() -> bool:
    return bool(client_id() and os.environ.get("STRAVA_CLIENT_SECRET"))


def _fernet() -> Fernet:
    s = _secreto()
    if not s:
        raise StravaError("Falta STRAVA_CLIENT_SECRET en el servidor.")
    clave = hashlib.sha256(("pacepath-cookie:" + s).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(clave))


@dataclass
class Sesion:
    access_token: str
    refresh_token: str
    expires_at: int
    atleta_id: int
    nombre: str = ""
    alcance: str = ""

    def cifrar(self) -> str:
        return _fernet().encrypt(json.dumps(asdict(self)).encode()).decode()

    @classmethod
    def descifrar(cls, valor: str | None) -> "Sesion | None":
        if not valor:
            return None
        try:
            return cls(**json.loads(_fernet().decrypt(valor.encode())))
        except (InvalidToken, ValueError, TypeError, StravaError):
            return None


# ── HTTP (una función, para poder simularla en los tests) ───────────────────

def _http(metodo: str, url: str, datos: dict | None = None,
          token: str | None = None) -> dict | list:
    cuerpo = urllib.parse.urlencode(datos).encode() if datos is not None else None
    req = urllib.request.Request(url, data=cuerpo, method=metodo,
                                 headers={"User-Agent": "PacePath/0.1"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise StravaError(tr("Strava pide esperar un poco (límite de uso de la API). "
                                 "Prueba en 15 minutos.",
                                 "Strava asks us to wait (API rate limit). Try again in "
                                 "15 minutes.")) from exc
        if exc.code in (401, 403):
            raise StravaError(tr("Strava rechazó el acceso: vuelve a conectar.",
                                 "Strava rejected access: please reconnect.")) from exc
        raise StravaError(tr(f"Strava respondió {exc.code}.",
                             f"Strava answered {exc.code}.")) from exc
    except OSError as exc:
        raise StravaError(tr("No pude hablar con Strava.", "I couldn't reach Strava.")) from exc


# ── OAuth ───────────────────────────────────────────────────────────────────

def nuevo_estado() -> str:
    """Valor aleatorio contra CSRF: va en una cookie y vuelve en la URL."""
    return secrets.token_urlsafe(24)


def url_autorizar(vuelta: str, estado: str) -> str:
    return AUTORIZAR + "?" + urllib.parse.urlencode({
        "client_id": client_id(), "redirect_uri": vuelta, "response_type": "code",
        "approval_prompt": "auto", "scope": ALCANCE, "state": estado,
    })


def _sesion_de(r: dict, alcance: str = "", anterior: Sesion | None = None) -> Sesion:
    atleta = r.get("athlete") or {}
    return Sesion(
        access_token=r["access_token"], refresh_token=r["refresh_token"],
        expires_at=int(r["expires_at"]),
        atleta_id=int(atleta.get("id") or (anterior.atleta_id if anterior else 0)),
        nombre=atleta.get("firstname") or (anterior.nombre if anterior else ""),
        alcance=alcance or (anterior.alcance if anterior else ""),
    )


def canjear_codigo(codigo: str, alcance: str) -> Sesion:
    r = _http("POST", TOKEN, {
        "client_id": client_id(), "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "code": codigo, "grant_type": "authorization_code",
    })
    return _sesion_de(r, alcance)


def vigente(s: Sesion) -> tuple[Sesion, bool]:
    """(sesión con token válido, ¿cambió?). Strava da tokens de 6 horas."""
    if s.expires_at - 300 > time.time():
        return s, False
    r = _http("POST", TOKEN, {
        "client_id": client_id(), "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "refresh_token": s.refresh_token, "grant_type": "refresh_token",
    })
    return _sesion_de(r, anterior=s), True


def revocar(s: Sesion) -> None:
    try:
        _http("POST", REVOCAR, {"access_token": s.access_token})
    except StravaError:
        pass        # aunque falle, se borra todo de nuestro lado


# ── Actividades ─────────────────────────────────────────────────────────────

def carreras(s: Sesion, pagina: int = 1, despues: int | None = None,
             por_pagina: int = 100) -> tuple[list[dict], int]:
    """Una página de carreras (sólo lo necesario para el mapa) y cuántas
    actividades de cualquier tipo traía la página: si trae menos que
    `por_pagina`, era la última."""
    q = {"page": pagina, "per_page": por_pagina}
    if despues:
        q["after"] = int(despues)
    datos = _http("GET", f"{API}/athlete/activities?" + urllib.parse.urlencode(q),
                  token=s.access_token)
    out = []
    for a in datos if isinstance(datos, list) else []:
        tipo = a.get("sport_type") or a.get("type")
        linea = (a.get("map") or {}).get("summary_polyline")
        if tipo not in CARRERA or not linea:
            continue
        out.append({
            "id": a["id"], "nombre": a.get("name", ""),
            "fecha": a.get("start_date", ""), "fecha_local": a.get("start_date_local", ""),
            "distancia_m": round(a.get("distance", 0)),
            "tiempo_s": int(a.get("moving_time") or 0),
            "desnivel_m": round(a.get("total_elevation_gain") or 0),
            "fc_media": a.get("average_heartrate"),
            # 1 = carrera, 2 = fondo, 3 = entrenamiento (lo que marca el atleta)
            "tipo_entreno": a.get("workout_type"),
            "polilinea": linea,
        })
    return out, len(datos) if isinstance(datos, list) else 0


def _totales(t: dict | None) -> dict:
    t = t or {}
    return {"carreras": int(t.get("count") or 0), "km": round((t.get("distance") or 0) / 1000, 1),
            "horas": round((t.get("moving_time") or 0) / 3600, 1),
            "desnivel_m": round(t.get("elevation_gain") or 0)}


FOTO_MAX_BYTES = 2_000_000


def foto(s: Sesion) -> tuple[bytes, str] | None:
    """La foto de perfil del propio atleta (la grande), para la tarjeta del
    Pasaporte. El servidor la trae porque el navegador no deja exportar un
    canvas con imágenes de otro dominio. La URL sale de la API de Strava, no
    del teléfono, y sólo se aceptan https."""
    a = _http("GET", f"{API}/athlete", token=s.access_token)
    url = a.get("profile") or a.get("profile_medium") or ""
    if not url.startswith("https://") or "avatar/athlete" in url:      # sin foto: el dibujo genérico
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "PacePath/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
            tipo = r.headers.get_content_type()
            datos = r.read(FOTO_MAX_BYTES + 1)
    except OSError as exc:
        raise StravaError(tr("No pude traer tu foto de Strava.", "I couldn't get your Strava photo.")) from exc
    if not tipo.startswith("image/") or len(datos) > FOTO_MAX_BYTES:
        return None
    return datos, tipo


def perfil(s: Sesion) -> dict:
    """Perfil público y totales de carrera (4 semanas, año, histórico) de Strava."""
    a = _http("GET", f"{API}/athlete", token=s.access_token)
    st = _http("GET", f"{API}/athletes/{s.atleta_id or a.get('id')}/stats", token=s.access_token)
    return {
        "nombre": a.get("firstname") or "", "apellido": a.get("lastname") or "",
        "foto": a.get("profile_medium") or a.get("profile"),
        "ciudad": ", ".join(x for x in (a.get("city"), a.get("country")) if x),
        "desde": (a.get("created_at") or "")[:10],
        "recientes": _totales(st.get("recent_run_totals")),     # últimas 4 semanas
        "anio": _totales(st.get("ytd_run_totals")),
        "historico": _totales(st.get("all_run_totals")),
    }
