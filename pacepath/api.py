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
    GET  /api/admin/acceso       beta cerrada: activa o no (misma cabecera)
    POST /api/admin/acceso       activarla o desactivarla
    GET/POST/PATCH/DELETE /api/admin/usuarios[/id]   cuentas (y quién es beta)
    GET  /api/admin/consumo      tamaño de la base y correos de las cuentas (panel «Consumo»)
    POST /api/consumo            el teléfono informa las teselas de mapa que bajó
    POST /api/auth/registro · /api/auth/entrar · /api/auth/salir   cuenta con correo
    GET  /api/auth/google · /api/auth/microsoft      entrar con Google o Microsoft
    GET  /api/cuenta             quién soy, si puedo entrar y si la beta está cerrada
    POST /api/auth/recuperar · /api/auth/nueva-clave · /api/auth/reenviar · GET /api/auth/verificar
                                 correo (apagado hasta configurar el envío: correo.py)
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
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

import numpy as np

from . import acceso, cancelar, ciudades, comunas, correo, cuentas, entreno, momento, oauth, frecuencia, idioma, lugares, nombres, recientes, registro, rendimiento, strava, uso, zonas
from .idioma import tr
from .gpx import plan_a_gpx
from .sesion import Sesion, interpretar
from .planificador import planificar
from .series import SinTramo
from .trazado import MAX_PUNTOS, TrazadoInvalido, trazar

WEB = Path(__file__).resolve().parents[1] / "web"



@asynccontextmanager
async def _vida(_app):
    threading.Thread(target=_precalentar, name="precalentar", daemon=True).start()
    threading.Thread(target=zonas.reanudar, name="zonas", daemon=True).start()
    yield
    registro.vaciar(5)            # al apagarse (Render duerme): guardar lo que quede en la cola


def _precalentar() -> None:
    """Al despertar, deja cargado el mapa de la comuna más pedida (últimos 14
    días; PACEPATH_PRECALENTAR la fija a mano, «no» lo apaga): así la primera
    ruta no paga la descarga y la carga del grafo. Si ya llegó un pedido, no
    se mete: el turno del motor está ocupado y ese pedido carga lo suyo."""
    pedida = os.environ.get("PACEPATH_PRECALENTAR", "").strip()
    if pedida == "no" or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        if not pedida:
            desde = (date.today() - timedelta(days=14)).isoformat()
            filas = registro.sql("SELECT comuna, COUNT(*) FROM pp_eventos WHERE consulta = 'ruta' AND dia >= ? "
                                 "AND comuna IS NOT NULL GROUP BY comuna ORDER BY COUNT(*) DESC LIMIT 1",
                                 (desde,), traer=True)
            pedida = filas[0][0] if filas else ""
        if pedida not in {c["id"] for c in comunas.disponibles()}:
            return
        if _motor.acquire(blocking=False):
            try:
                t = time.perf_counter()
                comunas.contexto(pedida)
                print(f"[precalentar] {pedida} listo en {time.perf_counter() - t:.1f} s", flush=True)
            finally:
                _motor.release()
                rendimiento.devolver_memoria()
    except Exception as exc:  # noqa: BLE001 — es una ayuda: si falla, el primer pedido lo carga
        print(f"[precalentar] no pude: {type(exc).__name__}: {exc}", flush=True)


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


# ── Acceso: siempre con cuenta; con la beta cerrada, sólo testers ──────────
# Sin sesión (o si la cuenta aún no puede entrar), las páginas de la app
# muestran la de entrada (Google, Microsoft o correo) y la API responde 401.
#   · Beta cerrada (se activa en /admin): sólo las cuentas marcadas como beta.
#   · Beta abierta: cualquiera que se registre.
#   · Con el envío de correos encendido (correo.py), quien se registró con
#     correo debe confirmarlo antes.
# Quedan abiertos el panel, el inicio de sesión, las páginas legales (Strava
# las exige públicas), la guía beta, los archivos fijos y la revisión de Render.
# PACEPATH_ACCESO_LIBRE=1 quita la cuenta obligatoria: sólo para tests y
# desarrollo local, nunca en Render.
_ABIERTAS = {"/admin", "/entrar", "/api/salud", "/api/uso", "/api/cuenta", "/manifest.webmanifest", "/sw.js",
             "/favicon.ico", "/privacidad", "/privacy", "/terminos", "/terms", "/beta"}
_ABIERTAS_PREFIJO = ("/api/admin/", "/api/auth/", "/vendor/", "/icons/", "/strava/", "/mapa/")


def _pendiente(u: dict | None) -> str | None:
    """Por qué esta sesión no puede usar la app (None: puede)."""
    if not u:
        return "sin_sesion"
    if cuentas.falta_verificar(u, correo.activo()):
        return "verificar"
    if acceso.activo() and not u["beta"]:
        return "beta"
    return None


def _usuario(request: Request) -> dict | None:
    try:
        return cuentas.de_cookie(request.cookies.get(cuentas.COOKIE))
    except Exception as exc:  # noqa: BLE001 — la base caída no tumba la app
        print(f"[cuentas] no pude leer la sesión: {type(exc).__name__}: {exc}", flush=True)
        return None


@app.middleware("http")
async def _con_cuenta(request: Request, call_next):
    ruta = request.url.path
    if (ruta in _ABIERTAS or ruta.startswith(_ABIERTAS_PREFIJO)
            or os.environ.get("PACEPATH_ACCESO_LIBRE") == "1"
            or _pendiente(_usuario(request)) is None):
        return await call_next(request)
    _anotar(evento="sin_acceso")
    if ruta.startswith("/api/"):
        return JSONResponse({"detail": "Entra con tu cuenta de PacePath."},
                            status_code=401, headers={"X-PacePath-Beta": "1"})
    return FileResponse(WEB / "entrar.html", headers={"Cache-Control": "no-store"})


# ── Registro de consultas (panel /admin) ────────────────────────────────────
# Qué se registra: sólo estas direcciones (no los archivos, ni /api/salud, que
# Render consulta cada pocos segundos, ni el propio panel).
CONSULTAS = {
    "/": "visita", "/privacidad": "privacidad", "/privacy": "privacidad", "/beta": "guia_beta",
    "/terminos": "terminos", "/terms": "terminos",
    "/api/entreno/revision": "entreno", "/api/strava/histograma": "strava_histograma",
    "/api/ruta": "ruta", "/api/ruta.gpx": "gpx", "/api/trazar": "trazado", "/api/trazar.gpx": "gpx",
    "/api/sesion": "interpretar", "/api/lugares": "busqueda", "/api/donde": "donde_corri",
    "/api/comunas": "comunas", "/api/evento": "evento",
    "/api/strava/conectar": "strava_conectar", "/api/strava/vuelta": "strava_conexion",
    "/api/strava/actividades": "strava_sync", "/api/strava/perfil": "strava_perfil",
    "/api/strava/foto": "strava_foto", "/api/strava/frecuencia": "strava_calles",
    "/api/strava/desconectar": "strava_desconectar",
    "/api/auth/registro": "cuenta_registro", "/api/auth/entrar": "cuenta_entrar", "/api/auth/salir": "cuenta_salir",
    "/api/auth/google/vuelta": "cuenta_google", "/api/auth/microsoft/vuelta": "cuenta_microsoft",
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
    if consulta is None or request.scope.get("pp_head"):
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
    cuenta = _usuario(request)
    info["cuenta"] = cuenta["id"] if cuenta else None
    largo = request.headers.get("content-length", "")
    info["bytes_in"] = int(largo) if largo.isdigit() else 0
    vivo = rendimiento.empezar(info)
    marca = uso.consulta.set(info)
    t0 = time.perf_counter()

    def terminar() -> None:
        info.setdefault("ms", round((time.perf_counter() - t0) * 1000))
        info.setdefault("exito", info.get("estado", 500) < 400)
        info.setdefault("evento", consulta)
        rendimiento.terminar(vivo, info)
        registro.anotar(info)
    try:
        resp = await call_next(request)
    except Exception as exc:
        info.update(estado=500, exito=False, error=f"{type(exc).__name__}: {exc}")
        uso.consulta.reset(marca)
        terminar()
        raise
    uso.consulta.reset(marca)
    info["estado"] = resp.status_code
    info["ms"] = round((time.perf_counter() - t0) * 1000)      # cálculo, sin el envío
    rendimiento.etapa(vivo, "enviando")
    if nuevo:
        resp.set_cookie(COOKIE_USUARIO, usuario, max_age=400 * 24 * 3600, httponly=True,
                        samesite="lax", secure=_base(request).startswith("https"))
    # Los bytes que salen (ya comprimidos: GZip va por dentro), contados al
    # terminar de enviarse; recién ahí se anota la fila.
    cuerpo = resp.body_iterator

    async def contar():
        n = 0
        try:
            async for trozo in cuerpo:
                n += len(trozo)
                yield trozo
        finally:
            info["bytes_out"] = n
            terminar()
    resp.body_iterator = contar()
    return resp


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


@app.middleware("http")
async def _head(request: Request, call_next):
    """HEAD responde como GET, sin cuerpo. Los filtros de correo (Outlook,
    Gmail, el de la UC…) revisan los enlaces con HEAD y un 405 les parece
    sospechoso. No se registra como visita: es un robot."""
    if request.method != "HEAD":
        return await call_next(request)
    request.scope["method"] = "GET"
    request.scope["pp_head"] = True
    resp = await call_next(request)
    async for _ in resp.body_iterator:     # se descarta (y así terminan los middlewares de adentro)
        pass
    headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
    return Response(status_code=resp.status_code, headers=headers)


@app.middleware("http")
async def _strava_en_cuenta(request: Request, call_next):
    """La conexión con Strava viaja con la cuenta: si este aparato no la tiene
    (otro teléfono, datos borrados, Safari que limpia), se repone desde la
    cuenta; y cada vez que Strava la crea, renueva o corta, se guarda ahí. Así,
    al entrar en otro aparato, Strava ya está conectado y sólo se baja lo que
    falte. Lo guardado es la autorización (cifrada), no las carreras."""
    if not request.url.path.startswith("/api/strava/"):
        return await call_next(request)
    u = _usuario(request)
    tenia = request.cookies.get(strava.COOKIE)
    guardada = cuentas.strava_de(u["id"]) if u else None
    repuesta = None
    if u and not tenia and guardada:
        request.scope["headers"] = [*request.scope["headers"], (b"cookie", f"{strava.COOKIE}={guardada}".encode())]
        repuesta = guardada
    resp = await call_next(request)
    if not u:
        return resp
    nueva = None
    for v in resp.headers.getlist("set-cookie"):
        if v.startswith(f"{strava.COOKIE}="):
            valor = v.split(";", 1)[0].split("=", 1)[1].strip('"')
            nueva = "" if not valor or "max-age=0" in v.lower() else valor
    if nueva is not None:                          # la conectó, renovó o desconectó
        if (nueva or None) != guardada:
            cuentas.guardar_strava(u["id"], nueva or None)
    elif tenia and tenia != guardada:              # conectada antes de que se guardara en la cuenta
        cuentas.guardar_strava(u["id"], tenia)
    elif repuesta:                                  # queda también en este aparato
        _poner_cookie(resp, request, strava.COOKIE, repuesta, 180 * 24 * 3600)
    return resp


# Preferencias que viajan con la cuenta (lo del teléfono que no es de Strava).
DATOS_CUENTA = {"pp_entreno_ajustes", "pp_fase", "pp_segun_hora", "pp_forma", "pp_radio", "pp_favoritos",
                "pp_guardadas", "pp_recientes", "pp_usar_frec", "pp_lang", "pp_noche", "pp_logros_vistos",
                "pp_pasaporte_abierto"}


class DatosCuenta(BaseModel):
    datos: dict[str, str]


@app.get("/api/cuenta/datos", include_in_schema=False)
def cuenta_datos(request: Request) -> dict:
    u = _usuario(request)
    if not u:
        raise HTTPException(401, "Not signed in")
    datos, ts = cuentas.datos_de(u["id"])
    return {"datos": datos, "actualizado": ts}


@app.put("/api/cuenta/datos", include_in_schema=False)
def cuenta_guardar_datos(body: DatosCuenta, request: Request) -> dict:
    u = _usuario(request)
    if not u:
        raise HTTPException(401, "Not signed in")
    try:
        ts = cuentas.guardar_datos(u["id"], {k: v for k, v in body.datos.items() if k in DATOS_CUENTA})
    except cuentas.ErrorCuenta as exc:
        raise _error_cuenta(exc, 413) from exc
    return {"actualizado": ts}


# La más externa (se agrega al final): escucha si el teléfono corta la conexión.
app.add_middleware(cancelar.VigilarConexion, rutas={"/api/ruta"})


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
    # False: que la hora no importe (sin ajustes de noche, calor ni hora punta).
    segun_hora: bool = True
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


EVENTOS_CLIENTE = {"error_js", "comentario", "compartir_pasaporte", "instalar", "sync", "sync_error", "mapa_error", "aporte", "gpx"}


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


# ── Consumo del lado del teléfono (panel «Consumo») ────────────────────────
# Las teselas del mapa no pasan por el servidor: el navegador las baja directo
# de Cloudflare R2 (mapa propio), AWS (relieve) y Esri. El teléfono cuenta
# cuántas bajó de cada uno y cuánto pesaron, y lo manda en resúmenes.
PROVEEDORES_MAPA = {"mapa_propio", "relieve", "esri", "otro"}


class ResumenMapa(BaseModel):
    tipo: str = Field(max_length=20)
    teselas: int = Field(ge=0, le=100_000)
    bytes: int = Field(ge=0, le=2_000_000_000)
    estimado: bool = False


class Consumo(BaseModel):
    mapas: list[ResumenMapa] = Field(max_length=10)


@app.post("/api/consumo", include_in_schema=False)
def consumo(body: Consumo, request: Request) -> dict:
    u = _usuario(request)
    anonimo = request.cookies.get(COOKIE_USUARIO, "")
    for m in body.mapas:
        if m.tipo not in PROVEEDORES_MAPA or not m.teselas:
            continue
        registro.anotar({"consulta": "consumo_mapa", "evento": "consumo_mapa", "tipo": m.tipo,
                         "usuario": anonimo if re.fullmatch(r"[0-9a-f]{10}", anonimo) else None,
                         "cuenta": u["id"] if u else None, "exito": True, "estado": 200,
                         "detalle": "estimado" if m.estimado else None,
                         "bytes_out": m.bytes, "externas": min(m.teselas, 32_000)})
    return {"ok": True}


class PruebaCorreo(BaseModel):
    para: str = Field(max_length=200)


@app.post("/api/admin/correo/prueba", include_in_schema=False)
def admin_prueba_correo(body: PruebaCorreo, request: Request) -> dict:
    """Manda un correo de prueba y, si falla, devuelve el error tal cual."""
    _solo_admin(request)
    if not correo.activo():
        raise HTTPException(503, {"mensaje": "El envío está apagado: revisa las variables PACEPATH_CORREO_* en Render."})
    try:
        correo.prueba(cuentas.normalizar_correo(body.para))
    except cuentas.ErrorCuenta as exc:
        raise _error_cuenta(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, {"mensaje": f"No se pudo mandar: {type(exc).__name__}: {exc}"}) from exc
    return {"ok": True}


@app.get("/api/admin/consumo", include_in_schema=False)
def admin_consumo(request: Request) -> dict:
    """Para el panel «Consumo»: tamaño de la base (vs el límite del plan) y
    quién es cada cuenta (el registro guarda sólo su id)."""
    _solo_admin(request)
    return {"base": registro.tamanos(), "cuentas": {u["id"]: u["correo"] for u in cuentas.listar()}}


@app.get("/api/admin/motor", include_in_schema=False)
def admin_motor(request: Request) -> dict:
    """Para el panel «Motor»: el proceso ahora (memoria, grafos cargados, cola).
    Lo de cada pedido viene en la columna `motor` del registro."""
    _solo_admin(request)
    return rendimiento.estado()


@app.get("/api/admin/vivo", include_in_schema=False)
def admin_vivo(request: Request, desde: float = 0) -> dict:
    """Tarjeta «En vivo»: muestras del proceso desde `desde` y pedidos en curso."""
    _solo_admin(request)
    return rendimiento.vivo(desde)


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


# ── Cuentas: registro e inicio de sesión ───────────────────────────────────
_MENSAJES = {
    "correo_invalido": ("Ese correo no parece válido.", "That email doesn't look valid."),
    "correo_existe": ("Ya hay una cuenta con ese correo: entra con ella.", "There's already an account with that email: sign in."),
    "correo_existe_otro_metodo": ("Ya hay una cuenta con ese correo: entra con el método que usaste al crearla.",
                                  "There's already an account with that email: sign in the way you created it."),
    "clave_corta": (f"La clave debe tener al menos {cuentas.CLAVE_MIN} caracteres.",
                    f"The password must be at least {cuentas.CLAVE_MIN} characters."),
    "clave_incorrecta": ("Correo o clave incorrectos.", "Wrong email or password."),
    "demasiados_intentos": ("Demasiados intentos: espera unos minutos.", "Too many attempts: wait a few minutes."),
    "bloqueada": ("Esta cuenta está desactivada.", "This account is disabled."),
    "sin_correo": ("Tu cuenta no compartió un correo: prueba otro método.", "Your account didn't share an email: try another method."),
    "no_existe": ("Esa cuenta no existe.", "That account doesn't exist."),
    "usa_google": ("Esta cuenta se creó con Google: entra con «Continuar con Google» (tu clave de Gmail se escribe "
                   "en Google, nunca aquí). Si también quieres una clave para PacePath, usa «¿Olvidaste tu clave?».",
                   "This account was created with Google: use “Continue with Google” (your Gmail password is typed "
                   "on Google, never here). If you also want a PacePath password, use “Forgot your password?”."),
    "usa_microsoft": ("Esta cuenta se creó con Microsoft: entra con «Continuar con Microsoft» (tu clave de Outlook se "
                      "escribe en Microsoft, nunca aquí). Si también quieres una clave para PacePath, usa «¿Olvidaste tu clave?».",
                      "This account was created with Microsoft: use “Continue with Microsoft” (your Outlook password "
                      "is typed on Microsoft, never here). If you also want a PacePath password, use “Forgot your password?”."),
    "datos_grandes": ("Tus preferencias guardadas superan el máximo: borra rutas guardadas antiguas.",
                      "Your saved preferences are over the limit: delete some old saved routes."),
    "codigo_invalido": ("El enlace venció o ya se usó: pide uno nuevo.", "The link expired or was already used: ask for a new one."),
    "correo_apagado": ("La recuperación por correo todavía no está disponible: escríbenos y te ayudamos.",
                       "Email recovery isn't available yet: write to us and we'll help."),
}


def _error_cuenta(exc: cuentas.ErrorCuenta, codigo: int = 400) -> HTTPException:
    es, en_ = _MENSAJES.get(str(exc), (str(exc), str(exc)))
    return HTTPException(codigo, {"codigo": str(exc), "mensaje": tr(es, en_)})


def _con_sesion(u: dict, request: Request, resp: Response) -> Response:
    if u.get("idioma") != idioma.actual():          # sus correos, en el idioma con que usa la app
        cuentas.actualizar(u["id"], idioma=idioma.actual())
    _poner_cookie(resp, request, cuentas.COOKIE, cuentas.abrir_sesion(u), cuentas.DURACION)
    # La conexión con Strava de otra cuenta (aparato compartido) no se hereda:
    # la de esta cuenta vuelve sola desde lo guardado (_strava_en_cuenta).
    resp.delete_cookie(strava.COOKIE)
    return resp


class Registro(BaseModel):
    correo: str = Field(max_length=200)
    clave: str = Field(max_length=200)
    nombre: str | None = Field(default=None, max_length=80)


class Ingreso(BaseModel):
    correo: str = Field(max_length=200)
    clave: str = Field(max_length=200)


@app.post("/api/auth/registro", include_in_schema=False)
def auth_registro(body: Registro, request: Request) -> Response:
    try:
        u = cuentas.crear(body.correo, body.nombre, body.clave, idioma=idioma.actual())
    except cuentas.ErrorCuenta as exc:
        _anotar(exito=False, error=str(exc))
        raise _error_cuenta(exc, 409 if str(exc) == "correo_existe" else 400) from exc
    uso.registrar("cuenta_nueva", "correo")
    _mandar_verificacion(u, request)
    if correo.activo():
        correo.en_fondo(correo.aviso_registro, u["correo"], u["nombre"], "correo y clave", _espera_beta(u), lang="es")
    return _con_sesion(u, request, JSONResponse({"usuario": cuentas.publico(u)}))


def _espera_beta(u: dict) -> bool:
    """Con la beta cerrada y sin estar habilitada: queda en la lista de espera."""
    return acceso.activo() and not u["beta"]


def _mandar_verificacion(u: dict, request: Request) -> None:
    if not correo.activo():
        return
    try:
        enlace = f"{_base(request)}/api/auth/verificar?codigo={cuentas.nuevo_codigo(u['id'], 'verificar')}"
        correo.verificar(u["correo"], u["nombre"], enlace, _espera_beta(u))
    except Exception as exc:  # noqa: BLE001 — el correo caído no impide crear la cuenta
        _anotar(error=f"no pude mandar el correo de verificación: {type(exc).__name__}: {exc}")


@app.post("/api/auth/reenviar", include_in_schema=False)
def auth_reenviar(request: Request) -> dict:
    """Vuelve a mandar el correo para confirmar la cuenta."""
    u = _usuario(request)
    if not u:
        raise HTTPException(401, "Not signed in")
    if not correo.activo():
        raise _error_cuenta(cuentas.ErrorCuenta("correo_apagado"), 503)
    _mandar_verificacion(u, request)
    return {"ok": True}


@app.get("/api/auth/verificar", include_in_schema=False)
def auth_verificar(codigo: str = "") -> Response:
    try:
        u = cuentas.usar_codigo(codigo, "verificar")
    except cuentas.ErrorCuenta:
        return RedirectResponse("/?cuenta=codigo_invalido")
    u = cuentas.actualizar(u["id"], verificado=True)
    uso.registrar("cuenta_verificada")
    if correo.activo():
        correo.en_fondo(correo.bienvenida, u["correo"], u["nombre"], _espera_beta(u), lang=u["idioma"])
    return RedirectResponse("/?cuenta=verificado")


class PedidoRecuperar(BaseModel):
    correo: str = Field(max_length=200)


@app.post("/api/auth/recuperar", include_in_schema=False)
def auth_recuperar(body: PedidoRecuperar, request: Request) -> dict:
    """Manda el enlace para elegir una clave nueva. Responde lo mismo exista o
    no la cuenta (que no sirva para averiguar quién está registrado)."""
    if not correo.activo():
        raise _error_cuenta(cuentas.ErrorCuenta("correo_apagado"), 503)
    try:
        u = cuentas.por_correo(cuentas.normalizar_correo(body.correo))
    except cuentas.ErrorCuenta as exc:
        raise _error_cuenta(exc) from exc
    if u and u["activo"]:
        try:
            enlace = f"{_base(request)}/entrar?recuperar={cuentas.nuevo_codigo(u['id'], 'recuperar')}"
            correo.recuperar(u["correo"], u["nombre"], enlace)
        except Exception as exc:  # noqa: BLE001
            _anotar(error=f"no pude mandar el correo de recuperación: {type(exc).__name__}: {exc}")
    return {"ok": True}


class NuevaClave(BaseModel):
    codigo: str = Field(max_length=200)
    clave: str = Field(max_length=200)


@app.post("/api/auth/nueva-clave", include_in_schema=False)
def auth_nueva_clave(body: NuevaClave, request: Request) -> Response:
    """Clave nueva con el enlace del correo: cierra las demás sesiones y, como
    el enlace llegó a su correo, lo da por verificado."""
    if len(body.clave) < cuentas.CLAVE_MIN:
        raise _error_cuenta(cuentas.ErrorCuenta("clave_corta"))
    try:
        u = cuentas.usar_codigo(body.codigo, "recuperar")
        u = cuentas.actualizar(u["id"], clave=body.clave, verificado=True)
    except cuentas.ErrorCuenta as exc:
        raise _error_cuenta(exc) from exc
    uso.registrar("cuenta_nueva_clave")
    return _con_sesion(u, request, JSONResponse({"usuario": cuentas.publico(u)}))


@app.post("/api/auth/entrar", include_in_schema=False)
def auth_entrar(body: Ingreso, request: Request) -> Response:
    try:
        u = cuentas.entrar_con_clave(body.correo, body.clave)
    except cuentas.ErrorCuenta as exc:
        time.sleep(1)                       # que adivinar claves sea lento
        _anotar(exito=False, error=str(exc))
        raise _error_cuenta(exc, 429 if str(exc) == "demasiados_intentos" else 401) from exc
    uso.registrar("cuenta_entrar", "correo")
    return _con_sesion(u, request, JSONResponse({"usuario": cuentas.publico(u)}))


@app.post("/api/auth/salir", include_in_schema=False)
def auth_salir(request: Request) -> Response:
    cuentas.cerrar_sesion(request.cookies.get(cuentas.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(cuentas.COOKIE)
    resp.delete_cookie(strava.COOKIE)          # queda guardada en la cuenta, no en este aparato
    return resp


@app.get("/api/auth/proveedores", include_in_schema=False)
def auth_proveedores() -> dict:
    return oauth.configurados()


COOKIE_IDIOMA_OAUTH = "pp_idioma_oauth"


@app.get("/api/auth/{proveedor}", include_in_schema=False)
def auth_ir(proveedor: Literal["google", "microsoft"], request: Request, correo: str = "") -> Response:
    if not oauth.configurados()[proveedor]:
        raise HTTPException(503, tr("Ese inicio de sesión no está configurado.", "That sign-in isn't configured."))
    pista = correo.strip()[:200] if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", correo.strip()) else None
    url, estado = oauth.iniciar(proveedor, f"{_base(request)}/api/auth/{proveedor}/vuelta", pista)
    resp = RedirectResponse(url)
    _poner_cookie(resp, request, oauth.COOKIE_ESTADO, estado, 600)
    _poner_cookie(resp, request, COOKIE_IDIOMA_OAUTH, idioma.actual(), 600)   # para la vuelta (?lang= del botón)
    return resp


@app.get("/api/auth/{proveedor}/vuelta", include_in_schema=False)
def auth_vuelta(proveedor: Literal["google", "microsoft"], request: Request, code: str | None = None,
                state: str | None = None, error: str | None = None) -> Response:
    idioma.usar(request.cookies.get(COOKIE_IDIOMA_OAUTH))
    if error or not code:
        uso.registrar("cuenta_cancelada", proveedor)
        return RedirectResponse("/?cuenta=cancelada")
    try:
        datos = oauth.terminar(proveedor, code, state or "", request.cookies.get(oauth.COOKIE_ESTADO),
                               f"{_base(request)}/api/auth/{proveedor}/vuelta")
        u = cuentas.entrar_con_proveedor(proveedor, datos["sub"], datos["correo"], datos["nombre"],
                                         datos["correo_confiable"])
    except cuentas.ErrorCuenta as exc:
        uso.registrar("cuenta_error", proveedor, str(exc), exito=False)
        return RedirectResponse(f"/?cuenta={exc}")
    except oauth.ErrorOAuth as exc:
        uso.registrar("cuenta_error", proveedor, exito=False)
        _anotar(error=str(exc))
        return RedirectResponse("/?cuenta=error")
    uso.registrar("cuenta_entrar", proveedor)
    if u.get("nueva") and correo.activo():
        correo.en_fondo(correo.bienvenida, u["correo"], u["nombre"], _espera_beta(u), lang=u["idioma"])
        correo.en_fondo(correo.aviso_registro, u["correo"], u["nombre"], proveedor.capitalize(), _espera_beta(u),
                        lang="es")
    resp = _con_sesion(u, request, RedirectResponse("/"))
    resp.delete_cookie(oauth.COOKIE_ESTADO)
    return resp


@app.get("/entrar", include_in_schema=False)
def pagina_entrar() -> FileResponse:
    """Entrar o crear cuenta (también con la beta abierta)."""
    return FileResponse(WEB / "entrar.html", headers={"Cache-Control": "no-store"})


@app.get("/api/cuenta", include_in_schema=False)
def cuenta(request: Request) -> dict:
    """Quién soy (si hay sesión), si puedo usar la app y qué opciones hay."""
    u = _usuario(request)
    if u and request.headers.get("x-idioma") and u.get("idioma") != idioma.actual():
        u = cuentas.actualizar(u["id"], idioma=idioma.actual())     # cambió el idioma de la app
    return {"usuario": cuentas.publico(u) if u else None, "pendiente": _pendiente(u),
            "beta_cerrada": acceso.activo(), "proveedores": oauth.configurados(),
            "correo_activo": correo.activo()}


class AjustesAcceso(BaseModel):
    activo: bool | None = None


def _solo_admin(request: Request) -> None:
    if not _es_admin(request.headers.get("x-admin-clave", "")):
        time.sleep(1)
        raise HTTPException(404, "Not Found")


@app.get("/api/admin/acceso", include_in_schema=False)
def admin_acceso(request: Request) -> dict:
    _solo_admin(request)
    return acceso.estado() | {"correo_activo": correo.activo(), "correo_estado": correo.estado}


@app.post("/api/admin/acceso", include_in_schema=False)
def admin_cambiar_acceso(body: AjustesAcceso, request: Request) -> dict:
    """Activa o desactiva la beta cerrada (se guarda en la base: sobrevive a
    que Render duerma)."""
    _solo_admin(request)
    try:
        return acceso.configurar(body.activo) | {"correo_activo": correo.activo(), "correo_estado": correo.estado}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"No pude guardar en la base de datos: {type(exc).__name__}") from exc


class NuevoUsuario(BaseModel):
    correo: str = Field(max_length=200)
    nombre: str | None = Field(default=None, max_length=80)
    clave: str | None = Field(default=None, max_length=200)
    beta: bool = False
    avisar: bool = True          # con beta: mandarle la invitación por correo


class CambioUsuario(BaseModel):
    correo: str | None = Field(default=None, max_length=200)
    nombre: str | None = Field(default=None, max_length=80)
    clave: str | None = Field(default=None, max_length=200)
    beta: bool | None = None
    activo: bool | None = None
    avisar: bool = True          # al habilitarla en la beta: avisarle por correo


@app.get("/api/admin/usuarios", include_in_schema=False)
def admin_usuarios(request: Request) -> list[dict]:
    _solo_admin(request)
    return cuentas.listar()


@app.post("/api/admin/usuarios", include_in_schema=False)
def admin_crear_usuario(body: NuevoUsuario, request: Request) -> dict:
    """Crear una cuenta (p. ej. para habilitar a un tester antes de que entre:
    al entrar con Google o con ese correo, queda vinculada)."""
    _solo_admin(request)
    try:
        u = cuentas.crear(body.correo, body.nombre, body.clave or None, beta=body.beta)
    except cuentas.ErrorCuenta as exc:
        raise _error_cuenta(exc, 409 if str(exc) == "correo_existe" else 400) from exc
    if body.beta and body.avisar and acceso.activo() and correo.activo():
        correo.en_fondo(correo.acceso_beta, u["correo"], u["nombre"], True)
    return cuentas.publico(u)


@app.patch("/api/admin/usuarios/{uid}", include_in_schema=False)
def admin_cambiar_usuario(uid: str, body: CambioUsuario, request: Request) -> dict:
    _solo_admin(request)
    antes = cuentas.por_id(uid)
    try:
        u = cuentas.actualizar(uid, **body.model_dump(exclude_none=True, exclude={"avisar"}))
    except cuentas.ErrorCuenta as exc:
        raise _error_cuenta(exc, 404 if str(exc) == "no_existe" else 409 if str(exc) == "correo_existe" else 400) from exc
    # Recién habilitada en la beta: avisarle. Si nunca entró (la creó el panel), es una invitación.
    if antes and not antes["beta"] and u["beta"] and not (body.avisar and acceso.activo() and correo.activo()):
        print(f"[correo] habilitada en la beta sin aviso: avisar={body.avisar}, beta cerrada={acceso.activo()}, "
              f"correo encendido={correo.activo()}", flush=True)
    if (antes and not antes["beta"] and u["beta"] and u["activo"] and body.avisar
            and acceso.activo() and correo.activo()):
        nunca_entro = not (u.get("clave") or u.get("google") or u.get("microsoft"))
        correo.en_fondo(correo.acceso_beta, u["correo"], u["nombre"], nunca_entro, lang=u["idioma"])
    return cuentas.publico(u)


@app.delete("/api/admin/usuarios/{uid}", include_in_schema=False)
def admin_borrar_usuario(uid: str, request: Request) -> dict:
    _solo_admin(request)
    if not cuentas.por_id(uid):
        raise HTTPException(404, "Not Found")
    cuentas.borrar(uid)
    return {"ok": True}


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
def buscar_lugares(q: str, limite: int = 8, cerca: str | None = None) -> list[dict]:
    """Calles, esquinas («Pocuro con Tobalaba») y parques para elegir la salida.
    `cerca` («lat,lon», el centro del mapa): fuera de Chile se busca alrededor."""
    try:
        la, lo = (float(x) for x in (cerca or "").split(","))
        punto = (la, lo) if -90 <= la <= 90 and -180 <= lo <= 180 else None
    except ValueError:
        punto = None
    r = lugares.buscar(q, min(max(limite, 1), 20), cerca=punto)
    # Para ver en el panel cuántas búsquedas fallan, sin guardar el texto (son direcciones de casas).
    _anotar(detalle=" · ".join(filter(None, [
        "con número" if re.search(r"\d", q) else "sin número", f"{len(r)} resultados",
        "número exacto" if any(x.get("exacta") for x in r) else None,
        "fuera del mapa" if r and all(x.get("fuera") for x in r) else None])))
    return r


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
    if zonas.activo():
        raise HTTPException(422, tr("Todavía no hay mapa ahí. Pide una ruta saliendo desde ese punto y "
                                    "preparamos el mapa de la zona en unos minutos.",
                                    "There's no map there yet. Ask for a route starting from that point and "
                                    "we'll prepare the area's map in a few minutes."))
    raise HTTPException(422, tr(f"Todavía no hay mapa ahí. Por ahora PacePath funciona en: {comunas.zonas()}. "
                                "Cuéntanos tu comuna en «Enviar comentario».",
                                f"There's no map there yet. For now PacePath works in: {comunas.zonas(True)}. "
                                "Tell us your area with “Send feedback”."))


def _comuna_o_zona(lat: float, lon: float) -> str:
    """El grafo de la salida. Si no hay, se pide su zona (zonas.py): el
    teléfono recibe un 409 con `preparando` y espera a que esté."""
    if (comuna := comunas.comuna_para(lat, lon)) is not None:
        return comuna
    z = zonas.pedir(lat, lon)
    if z["estado"] == "lista":
        return z["id"]
    if z["estado"] == "pedida":
        raise HTTPException(409, {"preparando": z, "mensaje": tr(
            f"Es la primera vez que alguien corre en {z['nombre']}: estamos preparando su mapa. Tarda unos minutos.",
            f"This is the first time someone runs in {z['nombre']}: we're preparing its map. It takes a few minutes.")})
    if z["estado"] == "fallida":
        raise HTTPException(422, tr("No pudimos preparar el mapa de esa zona: puede que casi no tenga calles en "
                                    "OpenStreetMap. Prueba saliendo desde un punto cercano.",
                                    "We couldn't prepare the map for that area: it may have very few streets on "
                                    "OpenStreetMap. Try starting from a nearby point."))
    if z["estado"] == "limite":
        raise HTTPException(429, tr("Hoy ya se prepararon muchas zonas nuevas. Vuelve a intentarlo mañana o "
                                    "cuéntanos tu zona en «Enviar comentario».",
                                    "Many new areas were prepared today already. Try again tomorrow or tell us "
                                    "your area with “Send feedback”."))
    _fuera_de_mapa(lat, lon)


@app.get("/api/zona/{zid}")
def zona_estado(zid: str) -> dict:
    """¿Ya está el mapa de la zona que se está preparando? (el teléfono pregunta cada 15 s)"""
    return zonas.estado(zid)


def _plan(body: PedidoRuta) -> dict:
    try:
        s = body.sesion or interpretar(body.texto or "", body.ritmo_s_km)
    except ValueError as exc:
        uso.registrar("sesion_no_entendida", body.texto, exito=False)
        raise HTTPException(422, str(exc)) from exc
    if body.max_subida_m is not None:
        s = s.model_copy(update={"max_subida_m": body.max_subida_m})
    comuna = body.comuna or _comuna_o_zona(body.lat, body.lon)
    cuando = momento.momento_de(body.hora, body.lat, body.lon) if body.segun_hora else None
    detalle = (body.texto, body.forma if body.forma != "auto" else None,
               f"opción {body.opcion + 1}" if body.opcion else None, "sin volver" if not body.volver else None)
    # ¿Se calculó hace poco? Se responde sin hacer fila ni cargar el mapa.
    version = next((c.get("version") for c in comunas.disponibles() if c["id"] == comuna), None) or comuna
    clave = recientes.clave(body.model_dump(mode="json", exclude={"sesion"}) | {"sesion": s.model_dump(mode="json")},
                            version, (cuando.oscuro, cuando.calor, cuando.punta) if cuando else None, idioma.actual())
    if (plan := recientes.buscar(clave)) is not None:
        uso.registrar("ruta", *detalle, "caché", "strava" if plan["preferencias"]["frecuentes"] else None,
                      tipo=s.tipo.value, comuna=comuna)
        return plan
    try:
        with rendimiento.en_motor(_motor, comuna, body.texto or s.resumen, s.tipo.value) as med:
            ctx = med.contexto()
            meta = (body.meta.lat, body.meta.lon) if body.meta else None
            pref, info = _preferidas(ctx, body)
            plan = planificar(ctx, s, body.lat, body.lon, volver=body.volver, meta=meta,
                              preferidas=pref, radio_max_m=body.radio_max_m,
                              forma=body.forma, n_vueltas=body.vueltas, momento=cuando,
                              opcion=body.opcion)
            plan["preferencias"] = info
            med.calidad(plan)
            # Versión del mapa: el teléfono guarda la ruta para el historial y
            # la vuelve a pedir sólo si el mapa de la comuna cambió.
            plan["mapa"] = ctx.version
            uso.registrar("ruta", *detalle, "strava" if info["frecuentes"] else None, tipo=s.tipo.value, comuna=comuna)
        recientes.guardar(clave, plan)
        return plan
    except SinTramo as exc:
        uso.registrar("ruta_sin_tramo", body.texto, tipo=s.tipo.value, comuna=comuna, exito=False)
        raise HTTPException(404, str(exc)) from exc
    except cancelar.Cancelado as exc:
        uso.registrar("ruta_cancelada", body.texto, tipo=s.tipo.value, comuna=comuna, exito=False)
        raise HTTPException(499, tr("Cancelaste la ruta.", "You cancelled the route.")) from exc


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
    with rendimiento.en_motor(_motor, comuna, f"{len(body.polilineas)} carreras") as med:
        ctx = med.contexto()
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
async def ruta(body: PedidoRuta, request: Request) -> dict:
    return await _cancelable(request, _plan, body)


@app.post("/api/cancelar", include_in_schema=False)
def cancelar_pedido(pedido: str = "") -> dict:
    """El teléfono avisa que la persona tocó «Cancelar» (va además de cortar
    la conexión: detrás de un proxy el corte no siempre llega al servidor)."""
    return {"ok": bool(pedido) and cancelar.cancelar(pedido[:64])}


async def _cancelable(request: Request, fn, *args):
    """Corre `fn` en un hilo (como cualquier endpoint normal) atenta a que el
    teléfono corte la conexión (cancelar.VigilarConexion) o avise por
    /api/cancelar: el motor deja de calcular en el próximo punto de corte."""
    pedido = request.headers.get("x-pedido", "")[:64] or None
    ev = cancelar.nuevo(pedido, request.scope.get("pp_corte"))

    def trabajo():
        with cancelar.vigilar(ev):
            return fn(*args)
    try:
        return await run_in_threadpool(trabajo)
    finally:
        cancelar.soltar(pedido)


def _trazado(body: PedidoTrazado) -> dict:
    p0 = body.puntos[0]
    comuna = comunas.comuna_para(p0.lat, p0.lon)
    if comuna is None:
        _fuera_de_mapa(p0.lat, p0.lon)
    try:
        with rendimiento.en_motor(_motor, comuna, f"{len(body.puntos)} puntos") as med:
            plan = trazar(med.contexto(), [(p.lat, p.lon) for p in body.puntos], body.volver)
            med.calidad(plan)
            return plan
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
        lista, crudas, cinta = strava.carreras(s, max(1, pagina), despues)
    except strava.StravaError as exc:
        raise _error_strava(exc) from exc
    _anotar(detalle=f"página {pagina}{' · desde ' + time.strftime('%Y-%m-%d', time.gmtime(despues)) if despues else ''}"
                    f" · {crudas} actividades → {len(lista)} carreras")
    resp = JSONResponse({"carreras": lista, "cinta": cinta,
                         "ultima": crudas < 100, "pagina": pagina})
    if renovada:
        _poner_cookie(resp, request, strava.COOKIE, s.cifrar(), 180 * 24 * 3600)
    return resp


@app.get("/api/strava/histograma")
def strava_histograma(request: Request, id: int) -> Response:
    """Pulso y ritmo de una carrera, resumidos (para la revisión del
    entrenamiento). No se guarda: va directo al teléfono."""
    s = strava.Sesion.descifrar(request.cookies.get(strava.COOKIE))
    if s is None:
        raise HTTPException(401, tr("Conecta tu cuenta de Strava.", "Connect your Strava account."))
    try:
        s, renovada = strava.vigente(s)
        h = strava.histograma(s, id)
    except strava.StravaError as exc:
        raise _error_strava(exc) from exc
    resp = JSONResponse(h)
    if renovada:
        _poner_cookie(resp, request, strava.COOKIE, s.cifrar(), 180 * 24 * 3600)
    return resp


class Ajustes(BaseModel):
    fc_max: float | None = Field(default=None, ge=120, le=230)
    fc_reposo: float | None = Field(default=None, ge=30, le=110)
    fc_umbral: float | None = Field(default=None, ge=100, le=220)
    t5k_s: float | None = Field(default=None, ge=720, le=3600)
    t10k_s: float | None = Field(default=None, ge=1500, le=7200)
    t21k_s: float | None = Field(default=None, ge=3300, le=15_000)
    t42k_s: float | None = Field(default=None, ge=7000, le=30_000)
    ritmo_umbral_s: float | None = Field(default=None, ge=150, le=600)
    fase: Literal["basica", "especifica", "competencia"] = "basica"


class PedidoRevision(BaseModel):
    ajustes: Ajustes
    carreras: list[dict] = Field(default_factory=list, max_length=600)
    hoy: date | None = None


@app.post("/api/entreno/revision")
def entreno_revision(body: PedidoRevision) -> dict:
    """Zonas y revisión de los últimos 3 meses con las carreras que manda el
    teléfono (de Strava, guardadas allá). No se guarda nada."""
    _anotar(tipo=body.ajustes.fase, detalle=f"{len(body.carreras)} carreras")
    return entreno.revision(body.carreras, body.ajustes.model_dump(), hoy=body.hoy)


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
