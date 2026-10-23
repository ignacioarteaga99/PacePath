"""API de PacePath.

    GET  /                       interfaz móvil
    GET  /api/salud
    GET  /api/comunas            comunas con grafo en la Release «graphs»
    GET  /api/lugares?q=         buscar desde dónde salir (calle, esquina, parque)
    POST /api/donde              puntos → ciudad y país (GeoNames, sin servicios externos)
    POST /api/sesion             «6x1k» → sesión estructurada
    POST /api/ruta               sesión + salida → plan completo
    POST /api/ruta.gpx           lo mismo, en GPX
    POST /api/trazar             ruta a mano: puntos → ruta por la calle (+ .gpx)

    Strava (sin base de datos: tokens en cookie cifrada, carreras en el navegador)
    GET  /api/strava/conectar    → strava.com (autorizar)
    GET  /api/strava/vuelta      ← strava.com (código → tokens)
    GET  /api/strava/estado
    GET  /api/strava/actividades?pagina=&despues=
    POST /api/strava/frecuencia  tus carreras → calles de siempre (no se guarda)
    GET  /api/strava/perfil      perfil y totales (4 semanas, año, histórico)
    GET  /api/strava/foto        tu foto de perfil (para la tarjeta del Pasaporte)
    POST /api/evento             eventos anónimos del teléfono (errores, comentarios)
    GET  /api/uso?clave=         métricas de la beta (clave = PACEPATH_ADMIN)
    GET  /admin                  panel de administración (registro de consultas)
    GET  /api/admin/eventos      sus datos (cabecera X-Admin-Clave = PACEPATH_ADMIN)
    GET  /api/admin/acceso       beta cerrada: activa o no y el usuario (misma cabecera)
    POST /api/admin/acceso       activarla o desactivarla, cambiar usuario y clave
    POST /api/beta/entrar        entrar a la beta cerrada (usuario y clave de tester)
    GET  /privacidad             política de privacidad
    POST /api/strava/desconectar revoca el acceso en Strava y borra la cookie
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

import numpy as np

from . import acceso, ciudades, comunas, momento, frecuencia, idioma, lugares, nombres, registro, strava, uso
from .idioma import tr
from .gpx import plan_a_gpx
from .sesion import Sesion, interpretar
from .planificador import planificar
from .series import SinTramo
from .trazado import MAX_PUNTOS, TrazadoInvalido, trazar

WEB = Path(__file__).resolve().parents[1] / "web"



@asynccontextmanager
async def _vida(_app):
    yield
    registro.vaciar(5)            # al apagarse (Render duerme): guardar lo que quede en la cola


app = FastAPI(title="PacePath", version="0.1.0", lifespan=_vida)
# Una ruta a la vez: el A* de RaceEngine reutiliza buffers por grafo (no es
# seguro entre hilos) y en 512 MB tampoco caben dos comunas cargando a la vez.
_motor = threading.Lock()
app.mount("/vendor", StaticFiles(directory=WEB / "vendor"), name="vendor")
app.mount("/icons", StaticFiles(directory=WEB / "icons"), name="icons")
# Logos oficiales de Strava (guía de marca de la API), sin modificar.
app.mount("/strava", StaticFiles(directory=WEB / "strava"), name="strava")
# Estilo y miniaturas del mapa propio (las teselas están en Cloudflare R2).
app.mount("/mapa", StaticFiles(directory=WEB / "mapa"), name="mapa")

# ── Tráfico de Render (5 GB al mes en el plan gratis) ───────────────────────
# Comprimido todo lo que sea texto, y los archivos fijos guardados en el
# teléfono: librerías, íconos y logos una semana; el estilo del mapa una hora
# (cambia más). Después preguntan con ETag y, si no cambiaron, la respuesta
# es un «304» sin cuerpo.
app.add_middleware(GZipMiddleware, minimum_size=800)
_CACHE = (("/vendor/", "public, max-age=604800"), ("/icons/", "public, max-age=604800"),
          ("/strava/", "public, max-age=604800"), ("/mapa/", "public, max-age=3600"))


@app.middleware("http")
async def _cache_estaticos(request: Request, call_next):
    resp = await call_next(request)
    for prefijo, valor in _CACHE:
        if request.url.path.startswith(prefijo) and resp.status_code in (200, 304):
            resp.headers["Cache-Control"] = valor
            break
    return resp


@app.middleware("http")
async def _idioma(request: Request, call_next):
    """Idioma de la respuesta: cabecera X-Idioma (la app) o ?lang= (enlaces)."""
    idioma.usar(request.headers.get("x-idioma") or request.query_params.get("lang"))
    return await call_next(request)


# ── Beta cerrada: usuario y clave antes de entrar (se activa en /admin) ────
# Sin la cookie de tester, las páginas de la app muestran la de entrada y la
# API responde 401. Quedan abiertos el panel, las páginas legales (Strava las
# exige públicas), la guía beta, los archivos fijos y la revisión de Render.
_ABIERTAS = {"/admin", "/api/salud", "/api/beta/entrar", "/api/uso", "/manifest.webmanifest", "/sw.js",
             "/favicon.ico", "/privacidad", "/privacy", "/terminos", "/terms", "/beta"}
_ABIERTAS_PREFIJO = ("/api/admin/", "/vendor/", "/icons/", "/strava/", "/mapa/")


@app.middleware("http")
async def _beta_cerrada(request: Request, call_next):
    ruta = request.url.path
    if (ruta in _ABIERTAS or ruta.startswith(_ABIERTAS_PREFIJO) or not acceso.activo()
            or acceso.cookie_valida(request.cookies.get(acceso.COOKIE))):
        return await call_next(request)
    _anotar(evento="beta_cerrada")
    if ruta.startswith("/api/"):
        return JSONResponse({"detail": "PacePath está en beta cerrada: entra con tu usuario y clave."},
                            status_code=401, headers={"X-PacePath-Beta": "1"})
    return FileResponse(WEB / "entrar.html", headers={"Cache-Control": "no-store"})


# ── Registro de consultas (panel /admin) ────────────────────────────────────
# Qué se registra: sólo estas direcciones (no los archivos, ni /api/salud, que
# Render consulta cada pocos segundos, ni el propio panel).
CONSULTAS = {
    "/": "visita", "/privacidad": "privacidad", "/privacy": "privacidad", "/beta": "guia_beta",
    "/terminos": "terminos", "/terms": "terminos",
    "/api/ruta": "ruta", "/api/ruta.gpx": "gpx", "/api/trazar": "trazado", "/api/trazar.gpx": "gpx",
    "/api/sesion": "interpretar", "/api/lugares": "busqueda", "/api/donde": "donde_corri",
    "/api/comunas": "comunas", "/api/evento": "evento",
    "/api/strava/conectar": "strava_conectar", "/api/strava/vuelta": "strava_conexion",
    "/api/strava/actividades": "strava_sync", "/api/strava/perfil": "strava_perfil",
    "/api/strava/foto": "strava_foto", "/api/strava/frecuencia": "strava_calles",
    "/api/strava/desconectar": "strava_desconectar", "/api/beta/entrar": "beta_entrar",
}
# Identificador anónimo del teléfono: al azar, sin relación con nombre ni Strava.
COOKIE_USUARIO = "pp_u"
_DISPOSITIVOS = (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android"),
                 ("macintosh", "Mac"), ("windows", "Windows"), ("linux", "Linux"))


def _dispositivo(ua: str) -> str:
    ua = ua.lower()
    return next((nombre for clave, nombre in _DISPOSITIVOS if clave in ua), "otro")


@app.middleware("http")
async def _registrar(request: Request, call_next):
    consulta = CONSULTAS.get(request.url.path)
    if consulta is None:
        return await call_next(request)
    usuario = request.cookies.get(COOKIE_USUARIO, "")
    nuevo = not re.fullmatch(r"[0-9a-f]{10}", usuario)
    if nuevo:
        usuario = secrets.token_hex(5)
    lang = request.headers.get("x-idioma") or request.query_params.get("lang") \
        or request.headers.get("accept-language", "es")
    info: dict = {"consulta": consulta, "usuario": usuario, "strava": strava.COOKIE in request.cookies,
                  "idioma": "en" if lang.lower().startswith("en") else "es",
                  "disp": _dispositivo(request.headers.get("user-agent", ""))}
    marca = uso.consulta.set(info)
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
        info["estado"] = resp.status_code
        if nuevo:
            resp.set_cookie(COOKIE_USUARIO, usuario, max_age=400 * 24 * 3600, httponly=True,
                            samesite="lax", secure=_base(request).startswith("https"))
        return resp
    except Exception as exc:
        info.update(estado=500, exito=False, error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        uso.consulta.reset(marca)
        info["ms"] = round((time.perf_counter() - t0) * 1000)
        info.setdefault("exito", info.get("estado", 500) < 400)
        info.setdefault("evento", consulta)
        registro.anotar(info)


@app.middleware("http")
async def _dominio_propio(request: Request, call_next):
    """Con dominio propio (PACEPATH_URL), quien entra por *.onrender.com va al
    dominio. Sólo las páginas: la API sigue respondiendo en la dirección vieja
    para no romper una pestaña ya abierta (y Render revisa /api/salud ahí)."""
    destino = os.environ.get("PACEPATH_URL", "").rstrip("/")
    host = request.headers.get("host", "").split(":")[0].lower()
    if (destino and host.endswith(".onrender.com") and host not in destino
            and request.method in ("GET", "HEAD") and not request.url.path.startswith("/api/")):
        q = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(destino + request.url.path + q, status_code=301)
    return await call_next(request)


def _anotar(**campos) -> None:
    """Agrega datos a la consulta en curso (si se está registrando)."""
    info = uso.consulta.get()
    if info is not None:
        info.update({k: v for k, v in campos.items() if v is not None})


@app.exception_handler(StarletteHTTPException)
async def _error_http(request: Request, exc: StarletteHTTPException):
    _anotar(error=str(exc.detail))
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _error_validacion(request: Request, exc: RequestValidationError):
    _anotar(error="datos inválidos: " + "; ".join(
        f"{'.'.join(str(x) for x in e.get('loc', [])[1:])} {e.get('msg', '')}" for e in exc.errors()[:3]))
    return await request_validation_exception_handler(request, exc)


class Punto(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PedidoTrazado(BaseModel):
    puntos: list[Punto] = Field(min_length=2, max_length=MAX_PUNTOS)
    volver: bool = True          # la meta es la salida (lo habitual)


class TextoSesion(BaseModel):
    texto: str = Field(min_length=1, max_length=120)
    ritmo_s_km: float | None = Field(default=None, ge=150, le=720)


class PedidoDonde(BaseModel):
    """Centros de los grupos de salidas de tus carreras (los calcula el teléfono)."""
    puntos: list[tuple[float, float]] = Field(max_length=3000)


class Frecuentes(BaseModel):
    """Calles de siempre que el teléfono calculó antes con /api/strava/frecuencia."""
    version: str
    aristas: list[int] = Field(max_length=frecuencia.MAX_ARISTAS)
    veces: list[int] = Field(max_length=frecuencia.MAX_ARISTAS)


class PedidoRuta(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    texto: str | None = Field(default=None, max_length=120)
    sesion: Sesion | None = None
    comuna: str | None = None
    # Volver a la salida (lo habitual) o no. Sin volver, con `meta` la ruta
    # termina ahí; sin meta, donde se cumple lo pedido.
    volver: bool = True
    meta: Punto | None = None
    # Tempo, fondo y regenerativo: desnivel positivo máximo de toda la sesión.
    max_subida_m: float | None = Field(default=None, ge=0, le=5000)
    # Cualquier sesión: nada de la ruta a más de este radio de la salida.
    radio_max_m: float | None = Field(default=None, ge=300, le=30_000)
    # Volviendo a la salida: «auto» (lo mejor, a menudo ida y vuelta), «circuito»
    # (cerrar un círculo repitiendo lo menos posible) o «vueltas» a un circuito.
    forma: Literal["auto", "circuito", "vueltas"] = "auto"
    vueltas: int | None = Field(default=None, ge=2, le=30)
    # Ritmo habitual del corredor (s/km, de sus carreras de Strava; lo calcula
    # el teléfono): para sesiones por tiempo, «45 min suave».
    ritmo_s_km: float | None = Field(default=None, ge=150, le=720)
    # Cuándo sale a correr (la hora del teléfono): de noche, con calor o en hora
    # punta cambia lo que conviene. Sin ella, ahora.
    hora: datetime | None = None
    # «Otra opción»: la n-ésima ruta distinta (0 = la mejor).
    opcion: int = Field(default=0, ge=0, le=9)
    # Preferencias del corredor (vienen de su teléfono; no se guardan).
    frecuentes: Frecuentes | None = None
    favoritos: list[list[tuple[float, float]]] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _una_sesion(self) -> "PedidoRuta":
        if self.sesion is None and not self.texto:
            raise ValueError("falta la sesión: manda «texto» («6x1k») o «sesion»")
        return self


@app.get("/", include_in_schema=False)
def index(request: Request, origen: str | None = None) -> Response:
    uso.registrar("visita", "app instalada" if origen == "app" else None)
    # La clave de mapas de Esri va en la página: es pública a propósito (sólo
    # mapas base y sólo desde nuestros dominios, según la configuró Esri).
    # Lo mismo la dirección del mapa propio en Cloudflare R2 (PACEPATH_MAPA_URL).
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = lambda v: json.dumps(v).replace("<", "\\u003c")
    config = []
    if clave := os.environ.get("ESRI_API_KEY", "").strip():
        config.append(f"window.PP_ESRI = {js(clave)};")
    if mapa := os.environ.get("PACEPATH_MAPA_URL", "").strip().rstrip("/"):
        config.append(f"window.PP_MAPA = {js(mapa)};")
    # Link para apoyar el proyecto (Ko-fi, Mercado Pago…): sin él, no hay botón.
    if (aporte := os.environ.get("PACEPATH_APORTE_URL", "").strip()).startswith("https://"):
        config.append(f"window.PP_APORTE = {js(aporte)};")
    if config:
        html = html.replace("<!--PP_CONFIG-->", "<script>" + " ".join(config) + "</script>", 1)
    # Si el teléfono ya tiene esta versión, «304» sin cuerpo (no gasta tráfico).
    etiqueta = '"' + hashlib.sha1(html.encode()).hexdigest()[:16] + '"'
    cabeceras = {"ETag": etiqueta, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etiqueta:
        return Response(status_code=304, headers=cabeceras)
    return HTMLResponse(html, headers=cabeceras)


EVENTOS_CLIENTE = {"error_js", "comentario", "compartir_pasaporte", "instalar", "sync", "sync_error", "mapa_error", "aporte"}


class Evento(BaseModel):
    tipo: str = Field(max_length=40)
    detalle: str | None = Field(default=None, max_length=200)


@app.post("/api/evento", include_in_schema=False)
def evento(body: Evento) -> dict:
    """Eventos anónimos del teléfono (errores de la interfaz, comentarios enviados…)."""
    if body.tipo in EVENTOS_CLIENTE:
        fallo = body.tipo.endswith("error")
        uso.registrar(body.tipo, body.detalle, exito=not fallo)
        if fallo:
            _anotar(error=body.detalle)
    return {"ok": True}


@app.get("/api/uso", include_in_schema=False)
def ver_uso(clave: str = "") -> dict:
    """Métricas de la beta. Sólo con la clave de PACEPATH_ADMIN (variable en Render)."""
    admin = os.environ.get("PACEPATH_ADMIN", "")
    if not admin or not secrets.compare_digest(clave, admin):
        raise HTTPException(404, "Not Found")
    return uso.resumen()


def _es_admin(clave: str) -> bool:
    admin = os.environ.get("PACEPATH_ADMIN", "")
    return bool(admin) and secrets.compare_digest(clave.encode(), admin.encode())


@app.get("/admin", include_in_schema=False)
def panel_admin() -> FileResponse:
    return FileResponse(WEB / "admin.html", headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})


@app.get("/api/admin/eventos", include_in_schema=False)
def admin_eventos(request: Request, desde: str | None = None, hasta: str | None = None) -> dict:
    """Las consultas entre dos días (de Santiago). La clave va en una cabecera,
    no en la dirección, para que no quede en historiales ni logs."""
    if not _es_admin(request.headers.get("x-admin-clave", "")):
        time.sleep(1)                       # que adivinar la clave sea lento
        raise HTTPException(404, "Not Found")
    hoy = date.fromisoformat(registro.hoy())
    try:
        d = date.fromisoformat(desde) if desde else hoy - timedelta(days=29)
        h = date.fromisoformat(hasta) if hasta else hoy
    except ValueError as exc:
        raise HTTPException(422, "Fechas en formato AAAA-MM-DD.") from exc
    return registro.consultar(d.isoformat(), h.isoformat())


class Entrada(BaseModel):
    usuario: str = Field(max_length=80)
    clave: str = Field(max_length=200)


@app.post("/api/beta/entrar", include_in_schema=False)
def beta_entrar(body: Entrada, request: Request) -> JSONResponse:
    """Usuario y clave de tester → cookie de la beta cerrada (90 días)."""
    if not acceso.entrar(body.usuario, body.clave):
        time.sleep(1)                       # que adivinar la clave sea lento
        _anotar(exito=False, detalle=body.usuario[:40])
        raise HTTPException(401, tr("Usuario o clave incorrectos.", "Wrong username or password."))
    resp = JSONResponse({"ok": True})
    resp.set_cookie(acceso.COOKIE, acceso.ficha(), max_age=acceso.DURACION, httponly=True,
                    samesite="lax", secure=_base(request).startswith("https"))
    return resp


class AjustesAcceso(BaseModel):
    activo: bool | None = None
    usuario: str | None = Field(default=None, min_length=1, max_length=80)
    clave: str | None = Field(default=None, min_length=4, max_length=200)


def _solo_admin(request: Request) -> None:
    if not _es_admin(request.headers.get("x-admin-clave", "")):
        time.sleep(1)
        raise HTTPException(404, "Not Found")


@app.get("/api/admin/acceso", include_in_schema=False)
def admin_acceso(request: Request) -> dict:
    _solo_admin(request)
    return acceso.estado()


@app.post("/api/admin/acceso", include_in_schema=False)
def admin_cambiar_acceso(body: AjustesAcceso, request: Request) -> dict:
    """Activa o desactiva la beta cerrada y cambia el usuario o la clave (se
    guardan en la base: sobreviven a que Render duerma)."""
    _solo_admin(request)
    try:
        return acceso.configurar(body.activo, body.usuario, body.clave)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"No pude guardar en la base de datos: {type(exc).__name__}") from exc


# App instalable (PWA): manifiesto y service worker en la raíz (su alcance es «/»).
@app.get("/manifest.webmanifest", include_in_schema=False)
def manifiesto() -> FileResponse:
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(WEB / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/beta", include_in_schema=False)
def guia_beta() -> FileResponse:
    """Guía para quienes prueban la beta (se comparte por WhatsApp)."""
    return FileResponse(WEB / "beta.html")


@app.get("/terminos", include_in_schema=False)
@app.get("/terms", include_in_schema=False)
def terminos() -> FileResponse:
    return FileResponse(WEB / "terminos.html")


@app.get("/privacidad", include_in_schema=False)
@app.get("/privacy", include_in_schema=False)
def privacidad() -> FileResponse:
    return FileResponse(WEB / "privacidad.html")


@app.get("/api/salud")
def salud() -> dict:
    return {"ok": True}


@app.get("/api/comunas")
def lista_comunas() -> list[dict]:
    return comunas.disponibles()


@app.post("/api/donde")
def donde(body: PedidoDonde) -> list[dict | None]:
    """Ciudad y país de cada punto (para «Dónde has corrido»). Nada se guarda."""
    return [ciudades.nombrar(la, lo) if -90 <= la <= 90 and -180 <= lo <= 180 else None
            for la, lo in body.puntos]


@app.get("/api/lugares")
def buscar_lugares(q: str, limite: int = 8) -> list[dict]:
    """Calles, esquinas («Pocuro con Tobalaba») y parques para elegir la salida."""
    return lugares.buscar(q, min(max(limite, 1), 20))


@app.post("/api/sesion")
def sesion(body: TextoSesion) -> dict:
    _anotar(detalle=body.texto)
    try:
        s = interpretar(body.texto, body.ritmo_s_km)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    _anotar(tipo=s.tipo.value)
    return s.model_dump(mode="json") | {"resumen": s.resumen}


def _fuera_de_mapa(lat: float, lon: float):
    """Fuera de las comunas con grafo: decir cuáles hay y contar la que se pidió
    (sólo su nombre, para saber qué comunas agregar)."""
    lugar = ciudades.nombrar(lat, lon) or {}
    uso.registrar("fuera_de_mapa", lugar.get("pais"), comuna=lugar.get("barrio") or lugar.get("ciudad") or "?",
                  exito=False)
    hay = ", ".join(c["nombre"] for c in comunas.disponibles())
    raise HTTPException(422, tr(f"Todavía no hay mapa ahí. Por ahora PacePath funciona en: {hay}. "
                                "Cuéntanos tu comuna en «Enviar comentario».",
                                f"There's no map there yet. For now PacePath works in: {hay}. "
                                "Tell us your area with “Send feedback”."))


def _plan(body: PedidoRuta) -> dict:
    try:
        s = body.sesion or interpretar(body.texto or "", body.ritmo_s_km)
    except ValueError as exc:
        uso.registrar("sesion_no_entendida", body.texto, exito=False)
        raise HTTPException(422, str(exc)) from exc
    if body.max_subida_m is not None:
        s = s.model_copy(update={"max_subida_m": body.max_subida_m})
    comuna = body.comuna or comunas.comuna_para(body.lat, body.lon)
    if comuna is None:
        _fuera_de_mapa(body.lat, body.lon)
    try:
        with _motor:
            ctx = comunas.contexto(comuna)
            meta = (body.meta.lat, body.meta.lon) if body.meta else None
            pref, info = _preferidas(ctx, body)
            cuando = momento.momento_de(body.hora, body.lat, body.lon)
            plan = planificar(ctx, s, body.lat, body.lon, volver=body.volver, meta=meta,
                              preferidas=pref, radio_max_m=body.radio_max_m,
                              forma=body.forma, n_vueltas=body.vueltas, momento=cuando,
                              opcion=body.opcion)
            plan["preferencias"] = info
            uso.registrar("ruta", body.texto, body.forma if body.forma != "auto" else None,
                          f"opción {body.opcion + 1}" if body.opcion else None,
                          "sin volver" if not body.volver else None, "strava" if info["frecuentes"] else None,
                          tipo=s.tipo.value, comuna=comuna)
            return plan
    except SinTramo as exc:
        uso.registrar("ruta_sin_tramo", body.texto, tipo=s.tipo.value, comuna=comuna, exito=False)
        raise HTTPException(404, str(exc)) from exc


def _preferidas(ctx, body: PedidoRuta) -> tuple[np.ndarray | None, dict]:
    """float32[E]: 1 neutro; calles de siempre más baratas según cuántas veces
    pasaste (0,85 → 0,65); tramos favoritos aún más (0,45)."""
    info = {"frecuentes": 0, "favoritos": 0, "version_ok": True}
    if not body.frecuentes and not body.favoritos:
        return None, info
    pref = np.ones(ctx.graph.n_edges, dtype=np.float32)
    f = body.frecuentes
    if f and f.version != ctx.version:
        info["version_ok"] = False          # el mapa cambió: el teléfono debe recalcular
    elif f:
        a = np.asarray(f.aristas, dtype=np.int64)
        v = np.asarray(f.veces[:len(a)], dtype=np.int64)
        for minimo, factor in ((2, 0.85), (4, 0.75), (10, 0.65)):
            m = frecuencia.mascara(ctx, a[v >= minimo])
            pref[m] = np.minimum(pref[m], factor)
        info["frecuentes"] = int((v >= 2).sum())
    for trazado in (body.favoritos or [])[:20]:
        m = frecuencia.mascara(ctx, frecuencia.aristas_de(ctx, trazado[:5000]))
        pref[m] = np.minimum(pref[m], 0.45)
        info["favoritos"] += 1
    return (pref if (pref < 1).any() else None), info


class PedidoFrecuencia(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    polilineas: list[str] = Field(max_length=3000)


@app.post("/api/strava/frecuencia")
def strava_frecuencia(body: PedidoFrecuencia) -> dict:
    """Tus carreras (polilíneas de Strava que manda tu teléfono) → por qué
    calles de esta comuna pasas seguido. No se guarda nada: la respuesta vuelve
    al teléfono, que la adjunta al pedir rutas."""
    comuna = comunas.comuna_para(body.lat, body.lon)
    if comuna is None:
        _fuera_de_mapa(body.lat, body.lon)
    with _motor:
        ctx = comunas.contexto(comuna)
        trazados = [frecuencia.decodificar(p) for p in body.polilineas if p]
        a, v = frecuencia.frecuencia(ctx, trazados)
        version = ctx.version
        # Tus calles más repetidas (para el Pasaporte): por calle, las veces de
        # su tramo más corrido.
        calles: dict[str, int] = {}
        tabla = nombres.tabla(ctx)
        if tabla is not None and len(a):
            for n, k in zip(tabla.de_aristas(ctx, a), v.tolist()):
                if n and k > calles.get(n, 0):
                    calles[n] = k
    orden = np.argsort(-v)[:frecuencia.MAX_ARISTAS]
    orden = orden[v[orden] >= 2]
    top = sorted(calles.items(), key=lambda x: -x[1])[:10]
    return {"comuna": comuna, "version": version, "carreras": len(trazados),
            "aristas": a[orden].tolist(), "veces": v[orden].tolist(),
            "calles": [{"nombre": n, "veces": k} for n, k in top]}


@app.post("/api/ruta")
def ruta(body: PedidoRuta) -> dict:
    return _plan(body)


def _trazado(body: PedidoTrazado) -> dict:
    p0 = body.puntos[0]
    comuna = comunas.comuna_para(p0.lat, p0.lon)
    if comuna is None:
        _fuera_de_mapa(p0.lat, p0.lon)
    try:
        with _motor:
            ctx = comunas.contexto(comuna)
            return trazar(ctx, [(p.lat, p.lon) for p in body.puntos], body.volver)
    except TrazadoInvalido as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/trazar")
def trazar_ruta(body: PedidoTrazado) -> dict:
    """Ruta a mano: une los puntos por la calle, en orden (y vuelve a la salida)."""
    return _trazado(body)


@app.post("/api/trazar.gpx")
def trazar_gpx(body: PedidoTrazado) -> Response:
    uso.registrar("gpx", "trazado")
    return Response(plan_a_gpx(_trazado(body)), media_type="application/gpx+xml",
                    headers={"Content-Disposition": 'attachment; filename="pacepath-trazado.gpx"'})


@app.post("/api/ruta.gpx")
def ruta_gpx(body: PedidoRuta) -> Response:
    plan = _plan(body)
    uso.registrar("gpx", tipo=plan["sesion"]["tipo"], comuna=plan.get("comuna"))
    nombre = plan["sesion"]["resumen"].split(" (")[0].replace("×", "x").replace(" ", "")
    return Response(plan_a_gpx(plan), media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="pacepath-{nombre}.gpx"'})


# ── Strava ──────────────────────────────────────────────────────────────────

def _error_strava(exc: strava.StravaError) -> HTTPException:
    """429: hay que esperar (no reintentar); 401: volver a conectar; 502: lo demás."""
    if isinstance(exc, strava.StravaEspera):
        return HTTPException(429, str(exc))
    if isinstance(exc, strava.StravaSinAcceso):
        return HTTPException(401, str(exc))
    return HTTPException(502, str(exc))


def _base(request: Request) -> str:
    """URL pública de la app. Render termina el HTTPS antes de llegar aquí, así
    que el esquema real viene en X-Forwarded-Proto."""
    if os.environ.get("PACEPATH_URL"):
        return os.environ["PACEPATH_URL"].rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
    return f"{proto}://{host}"


def _poner_cookie(resp: Response, request: Request, nombre: str, valor: str,
                  max_age: int) -> None:
    resp.set_cookie(nombre, valor, max_age=max_age, httponly=True, samesite="lax",
                    secure=_base(request).startswith("https"))


@app.get("/api/strava/conectar", include_in_schema=False)
def strava_conectar(request: Request) -> Response:
    if not strava.configurado():
        raise HTTPException(503, tr("Strava no está configurado en el servidor.",
                                    "Strava isn't configured on the server."))
    estado = strava.nuevo_estado()
    resp = RedirectResponse(strava.url_autorizar(f"{_base(request)}/api/strava/vuelta", estado))
    _poner_cookie(resp, request, strava.COOKIE_ESTADO, estado, 600)
    return resp


@app.get("/api/strava/vuelta", include_in_schema=False)
def strava_vuelta(request: Request, code: str | None = None, state: str | None = None,
                  scope: str = "", error: str | None = None) -> Response:
    destino = "/#ciudad"
    if error or not code:
        uso.registrar("strava_cancelado", error)
        return RedirectResponse(destino + "-cancelado")
    if not state or state != request.cookies.get(strava.COOKIE_ESTADO):
        uso.registrar("strava_error", exito=False)
        _anotar(error="el estado de la vuelta no coincide (cookie vencida o perdida)")
        return RedirectResponse(destino + "-error")
    try:
        s = strava.canjear_codigo(code, scope)
    except strava.StravaLimite as exc:
        uso.registrar("strava_limite", exito=False)
        _anotar(error=str(exc))
        return RedirectResponse(destino + "-limite")
    except strava.StravaError as exc:
        uso.registrar("strava_error", exito=False)
        _anotar(error=str(exc))
        return RedirectResponse(destino + "-error")
    if "activity:read" in scope:
        uso.registrar("strava_conectado")
    else:
        uso.registrar("strava_conectado", "sin permiso para actividades", exito=False)
    resp = RedirectResponse(destino if "activity:read" in scope else destino + "-sin-permiso")
    _poner_cookie(resp, request, strava.COOKIE, s.cifrar(), 180 * 24 * 3600)
    resp.delete_cookie(strava.COOKIE_ESTADO)
    return resp


@app.get("/api/strava/estado")
def strava_estado(request: Request) -> dict:
    s = strava.Sesion.descifrar(request.cookies.get(strava.COOKIE))
    return {"configurado": strava.configurado(), "conectado": s is not None,
            # Para diagnosticar «redirect_uri invalid»: el dominio de esta URL
            # debe ser el «Authorization Callback Domain» de la app de Strava.
            "vuelta": f"{_base(request)}/api/strava/vuelta",
            "nombre": s.nombre if s else None,
            "privadas": bool(s and "activity:read_all" in s.alcance),
            # Mientras Strava no apruebe la app, sólo pocas cuentas pueden conectarse:
            # PACEPATH_STRAVA_ABIERTO=1 en Render cuando la aprueben (quita el aviso).
            "abierto": os.environ.get("PACEPATH_STRAVA_ABIERTO") == "1"}


@app.get("/api/strava/actividades")
def strava_actividades(request: Request, pagina: int = 1, despues: int | None = None) -> Response:
    s = strava.Sesion.descifrar(request.cookies.get(strava.COOKIE))
    if s is None:
        raise HTTPException(401, tr("Conecta tu cuenta de Strava.",
                                    "Connect your Strava account."))
    try:
        s, renovada = strava.vigente(s)
        lista, crudas = strava.carreras(s, max(1, pagina), despues)
    except strava.StravaError as exc:
        raise _error_strava(exc) from exc
    _anotar(detalle=f"página {pagina}{' · desde ' + time.strftime('%Y-%m-%d', time.gmtime(despues)) if despues else ''}"
                    f" · {crudas} actividades → {len(lista)} carreras")
    resp = JSONResponse({"carreras": lista, "ultima": crudas < 100, "pagina": pagina})
    if renovada:
        _poner_cookie(resp, request, strava.COOKIE, s.cifrar(), 180 * 24 * 3600)
    return resp


@app.get("/api/strava/foto")
def strava_foto(request: Request) -> Response:
    """Tu foto de perfil de Strava, servida desde aquí (para la tarjeta del Pasaporte)."""
    s = strava.Sesion.descifrar(request.cookies.get(strava.COOKIE))
    if s is None:
        raise HTTPException(401, tr("Conecta tu cuenta de Strava.", "Connect your Strava account."))
    try:
        s, renovada = strava.vigente(s)
        f = strava.foto(s)
    except strava.StravaError as exc:
        raise _error_strava(exc) from exc
    if f is None:
        raise HTTPException(404, tr("No tienes foto de perfil en Strava.", "You don't have a Strava profile photo."))
    resp = Response(f[0], media_type=f[1], headers={"Cache-Control": "private, max-age=3600"})
    if renovada:
        _poner_cookie(resp, request, strava.COOKIE, s.cifrar(), 180 * 24 * 3600)
    return resp


@app.get("/api/strava/perfil")
def strava_perfil(request: Request) -> Response:
    s = strava.Sesion.descifrar(request.cookies.get(strava.COOKIE))
    if s is None:
        raise HTTPException(401, tr("Conecta tu cuenta de Strava.",
                                    "Connect your Strava account."))
    try:
        s, renovada = strava.vigente(s)
        datos = strava.perfil(s)
    except strava.StravaError as exc:
        raise _error_strava(exc) from exc
    resp = JSONResponse(datos)
    if renovada:
        _poner_cookie(resp, request, strava.COOKIE, s.cifrar(), 180 * 24 * 3600)
    return resp


@app.post("/api/strava/desconectar")
def strava_desconectar(request: Request) -> Response:
    s = strava.Sesion.descifrar(request.cookies.get(strava.COOKIE))
    if s is not None:
        strava.revocar(s)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(strava.COOKIE)
    return resp
