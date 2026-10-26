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
import math
import os
import secrets
import threading
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


class StravaLimite(StravaError):
    """La app de Strava llegó a su tope de atletas conectados (1 hasta que
    Strava la revise y apruebe)."""


class StravaEspera(StravaError):
    """Límite de uso de la API (429): hay que esperar, reintentar ya no sirve."""


class StravaSinAcceso(StravaError):
    """Strava rechazó el token (401/403): hay que volver a conectar."""

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


# ── Cupo de la API (de toda la app, no por persona) ─────────────────────────
# Strava permite a la app ~200 lecturas cada 15 min y ~2.000 al día, contando a
# todos los usuarios juntos, y lo informa en cada respuesta
# (X-ReadRateLimit-Usage / -Limit). Al llegar al 90 % se frena aquí, con un
# mensaje amable, antes de que Strava responda 429 a todo el mundo. Las
# ventanas de Strava son los cuartos de hora en punto y el día en UTC.
FRENO = 0.9
_cupo = {"ventana": None, "dia": None, "uso15": 0, "usodia": 0, "lim15": 200, "limdia": 2000}
_lock_cupo = threading.Lock()


def _ventana(t: float) -> int:
    return int(t // 900)


def _dia(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


def _anotar_cupo(cabeceras, agotado: bool = False) -> None:
    ahora = time.time()
    try:
        u15, ud = (int(x) for x in cabeceras.get("X-ReadRateLimit-Usage", "").split(",")[:2])
        l15, ld = (int(x) for x in cabeceras.get("X-ReadRateLimit-Limit", "").split(",")[:2])
    except (AttributeError, ValueError):
        if not agotado:
            return
        with _lock_cupo:                    # 429 sin cabeceras: dar por lleno este cuarto de hora
            _cupo.update(ventana=_ventana(ahora), uso15=_cupo["lim15"])
        return
    with _lock_cupo:
        _cupo.update(ventana=_ventana(ahora), dia=_dia(ahora), uso15=u15, usodia=ud, lim15=l15, limdia=ld)


def revisar_cupo() -> None:
    """Antes de leer de Strava: si el cupo de toda la app está casi agotado, esperar."""
    ahora = time.time()
    with _lock_cupo:
        c = dict(_cupo)
    if c["dia"] == _dia(ahora) and c["usodia"] >= FRENO * c["limdia"]:
        raise StravaEspera(tr("Strava ya recibió muchas consultas de PacePath hoy. Vuelve mañana: "
                              "tus carreras siguen guardadas en tu teléfono.",
                              "Strava has had a lot of requests from PacePath today. Come back tomorrow: "
                              "your runs are still saved on your phone.",
                              "O Strava recebeu muitos pedidos do PacePath hoje. Volte amanhã: "
                              "suas corridas continuam salvas no seu celular."))
    if c["ventana"] == _ventana(ahora) and c["uso15"] >= FRENO * c["lim15"]:
        minutos = max(1, math.ceil(((c["ventana"] + 1) * 900 - ahora) / 60))
        raise StravaEspera(tr(f"Strava está con mucha demanda de PacePath en este momento. "
                              f"Vuelve en {minutos} min y sigue donde quedó.",
                              f"Strava is getting a lot of requests from PacePath right now. "
                              f"Come back in {minutos} min to continue where it left off.",
                              f"O Strava está recebendo muitos pedidos do PacePath agora. "
                              f"Volte em {minutos} min para continuar de onde parou."))


# ── HTTP (una función, para poder simularla en los tests) ───────────────────

def _http(metodo: str, url: str, datos: dict | None = None,
          token: str | None = None) -> dict | list:
    cuerpo = urllib.parse.urlencode(datos).encode() if datos is not None else None
    req = urllib.request.Request(url, data=cuerpo, method=metodo,
                                 headers={"User-Agent": "PacePath/0.1"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    lectura = metodo == "GET" and token is not None          # cuenta en el cupo de lecturas
    if lectura:
        revisar_cupo()
    from . import uso
    uso.contar_externa()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
            if lectura:
                _anotar_cupo(r.headers)
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if lectura:
            _anotar_cupo(exc.headers or {}, agotado=exc.code == 429)
        if exc.code == 429:
            raise StravaEspera(tr("Strava pide esperar un poco (límite de uso de la API). "
                                 "Prueba en 15 minutos.",
                                 "Strava asks us to wait (API rate limit). Try again in "
                                 "15 minutes.",
                                 "O Strava pede para esperar (limite da API). Tente de novo em "
                                 "15 minutos.")) from exc
        if exc.code == 403:
            try:
                cuerpo = exc.read().decode("utf-8", "replace").lower()
            except Exception:  # noqa: BLE001
                cuerpo = ""
            if "limit" in cuerpo:
                raise StravaLimite(tr("Strava todavía limita cuántas personas pueden conectar "
                                      "PacePath (la app está en revisión).",
                                      "Strava still limits how many people can connect PacePath "
                                      "(the app is under review).",
                                      "O Strava ainda limita quantas pessoas podem conectar o PacePath "
                                      "(o app está em revisão).")) from exc
        if exc.code in (401, 403):
            raise StravaSinAcceso(tr("Strava rechazó el acceso: vuelve a conectar.",
                                 "Strava rejected access: please reconnect.",
                                 "O Strava recusou o acesso: conecte de novo.")) from exc
        raise StravaError(tr(f"Strava respondió {exc.code}.",
                             f"Strava answered {exc.code}.",
                             f"O Strava respondeu {exc.code}.")) from exc
    except OSError as exc:
        raise StravaError(tr("No pude hablar con Strava.", "I couldn't reach Strava.",
                                                           "Não consegui falar com o Strava.")) from exc


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
             por_pagina: int = 100) -> tuple[list[dict], int, list[dict]]:
    """Una página de carreras (sólo lo necesario para el mapa), cuántas
    actividades de cualquier tipo traía la página (si trae menos que
    `por_pagina`, era la última) y las carreras sin mapa (cinta)."""
    q = {"page": pagina, "per_page": por_pagina}
    if despues:
        q["after"] = int(despues)
    datos = _http("GET", f"{API}/athlete/activities?" + urllib.parse.urlencode(q),
                  token=s.access_token)
    out, cinta = [], []
    for a in datos if isinstance(datos, list) else []:
        tipo = a.get("sport_type") or a.get("type")
        linea = (a.get("map") or {}).get("summary_polyline")
        if tipo not in CARRERA:
            continue
        if not linea:
            # Cinta (o sin GPS): no va al mapa, pero cuenta para la revisión del entrenamiento.
            cinta.append(_resumen(a))
            continue
        out.append({
            "id": a["id"], "nombre": a.get("name", ""),
            "fecha": a.get("start_date", ""), "fecha_local": a.get("start_date_local", ""),
            "distancia_m": round(a.get("distance", 0)),
            "tiempo_s": int(a.get("moving_time") or 0),
            "desnivel_m": round(a.get("total_elevation_gain") or 0),
            "fc_media": a.get("average_heartrate"),
            "fc_max": a.get("max_heartrate"),
            # 1 = carrera, 2 = fondo, 3 = entrenamiento (lo que marca el atleta)
            "tipo_entreno": a.get("workout_type"),
            "polilinea": linea,
        })
    return out, len(datos) if isinstance(datos, list) else 0, cinta


def _resumen(a: dict) -> dict:
    return {"id": a["id"], "nombre": a.get("name", ""), "fecha": a.get("start_date", ""),
            "fecha_local": a.get("start_date_local", ""), "distancia_m": round(a.get("distance", 0)),
            "tiempo_s": int(a.get("moving_time") or 0), "fc_media": a.get("average_heartrate"),
            "fc_max": a.get("max_heartrate"), "tipo_entreno": a.get("workout_type"), "cinta": True}


def histograma(s: Sesion, actividad: int) -> dict:
    """Pulso y ritmo de una carrera, segundo a segundo, resumidos: segundos por
    pulsación ([bpm, s]) y por ritmo en tramos de 5 s/km ([s/km, s]), sólo en
    movimiento. Es lo que necesita la revisión del entrenamiento para repartir
    el tiempo en zonas (y recalcularlo si cambian las zonas) sin guardar la
    carrera entera: unos cientos de bytes en vez de decenas de KB."""
    d = _http("GET", f"{API}/activities/{int(actividad)}/streams?keys=heartrate,velocity_smooth,time,moving"
                     "&key_by_type=true", token=s.access_token)
    serie = lambda k: (d.get(k) or {}).get("data") or [] if isinstance(d, dict) else []  # noqa: E731
    t, fc, vel, mov = serie("time"), serie("heartrate"), serie("velocity_smooth"), serie("moving")
    fc_s: dict[int, float] = {}
    ritmo_s: dict[int, float] = {}
    total = 0.0
    for i in range(1, len(t)):
        dt = t[i] - t[i - 1]
        if not 0 < dt <= 10 or (mov and not mov[i]):          # pausas y saltos del GPS
            continue
        total += dt
        if i < len(fc) and fc[i] and 60 <= fc[i] <= 230:
            fc_s[int(fc[i])] = fc_s.get(int(fc[i]), 0) + dt
        if i < len(vel) and vel[i] and vel[i] > 1.0:          # más lento que 16:40/km: caminando
            r = min(1200, int(1000 / vel[i] // 5 * 5))
            ritmo_s[r] = ritmo_s.get(r, 0) + dt
    # FC máx. de la carrera sin los picos del sensor: lo que se sostuvo ≥ 5 s.
    fc_max, acum = None, 0.0
    for b in sorted(fc_s, reverse=True):
        acum += fc_s[b]
        if acum >= 5:
            fc_max = b
            break
    return {"fc": [[b, round(x)] for b, x in sorted(fc_s.items())],
            "ritmo": [[r, round(x)] for r, x in sorted(ritmo_s.items())],
            "fc_max": fc_max, "s": round(total)}


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
        raise StravaError(tr("No pude traer tu foto de Strava.", "I couldn't get your Strava photo.",
                                                                 "Não consegui pegar sua foto do Strava.")) from exc
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
