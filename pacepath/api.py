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
    POST /api/strava/desconectar revoca el acceso en Strava y borra la cookie
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

import numpy as np

from . import ciudades, comunas, frecuencia, idioma, lugares, nombres, strava
from .idioma import tr
from .gpx import plan_a_gpx
from .sesion import Sesion, interpretar
from .planificador import planificar
from .series import SinTramo
from .trazado import MAX_PUNTOS, TrazadoInvalido, trazar

WEB = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="PacePath", version="0.1.0")
# Una ruta a la vez: el A* de RaceEngine reutiliza buffers por grafo (no es
# seguro entre hilos) y en 512 MB tampoco caben dos comunas cargando a la vez.
_motor = threading.Lock()
app.mount("/vendor", StaticFiles(directory=WEB / "vendor"), name="vendor")


@app.middleware("http")
async def _idioma(request: Request, call_next):
    """Idioma de la respuesta: cabecera X-Idioma (la app) o ?lang= (enlaces)."""
    idioma.usar(request.headers.get("x-idioma") or request.query_params.get("lang"))
    return await call_next(request)


class Punto(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PedidoTrazado(BaseModel):
    puntos: list[Punto] = Field(min_length=2, max_length=MAX_PUNTOS)
    volver: bool = True          # la meta es la salida (lo habitual)


class TextoSesion(BaseModel):
    texto: str = Field(min_length=1, max_length=120)


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
    # Preferencias del corredor (vienen de su teléfono; no se guardan).
    frecuentes: Frecuentes | None = None
    favoritos: list[list[tuple[float, float]]] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _una_sesion(self) -> "PedidoRuta":
        if self.sesion is None and not self.texto:
            raise ValueError("falta la sesión: manda «texto» («6x1k») o «sesion»")
        return self


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


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
    try:
        s = interpretar(body.texto)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return s.model_dump(mode="json") | {"resumen": s.resumen}


def _plan(body: PedidoRuta) -> dict:
    try:
        s = body.sesion or interpretar(body.texto or "")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.max_subida_m is not None:
        s = s.model_copy(update={"max_subida_m": body.max_subida_m})
    comuna = body.comuna or comunas.comuna_para(body.lat, body.lon)
    if comuna is None:
        raise HTTPException(422, tr("Ese punto no está en ninguna comuna con mapa todavía.",
                                 "That point isn't in any area with a map yet."))
    try:
        with _motor:
            ctx = comunas.contexto(comuna)
            meta = (body.meta.lat, body.meta.lon) if body.meta else None
            pref, info = _preferidas(ctx, body)
            plan = planificar(ctx, s, body.lat, body.lon, volver=body.volver, meta=meta,
                              preferidas=pref, radio_max_m=body.radio_max_m,
                              forma=body.forma, n_vueltas=body.vueltas)
            plan["preferencias"] = info
            return plan
    except SinTramo as exc:
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
        raise HTTPException(422, tr("Ese punto no está en ninguna comuna con mapa todavía.",
                                 "That point isn't in any area with a map yet."))
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
        raise HTTPException(422, tr("Ese punto no está en ninguna comuna con mapa todavía.",
                                 "That point isn't in any area with a map yet."))
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
    return Response(plan_a_gpx(_trazado(body)), media_type="application/gpx+xml",
                    headers={"Content-Disposition": 'attachment; filename="pacepath-trazado.gpx"'})


@app.post("/api/ruta.gpx")
def ruta_gpx(body: PedidoRuta) -> Response:
    plan = _plan(body)
    nombre = plan["sesion"]["resumen"].split(" (")[0].replace("×", "x").replace(" ", "")
    return Response(plan_a_gpx(plan), media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="pacepath-{nombre}.gpx"'})


# ── Strava ──────────────────────────────────────────────────────────────────

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
        return RedirectResponse(destino + "-cancelado")
    if not state or state != request.cookies.get(strava.COOKIE_ESTADO):
        return RedirectResponse(destino + "-error")
    try:
        s = strava.canjear_codigo(code, scope)
    except strava.StravaError:
        return RedirectResponse(destino + "-error")
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
            "privadas": bool(s and "activity:read_all" in s.alcance)}


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
        raise HTTPException(502, str(exc)) from exc
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
        raise HTTPException(502, str(exc)) from exc
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
        raise HTTPException(502, str(exc)) from exc
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
