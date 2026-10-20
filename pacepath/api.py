"""API de PacePath.

    GET  /                       interfaz móvil
    GET  /api/salud
    GET  /api/comunas            comunas con grafo en la Release «graphs»
    GET  /api/lugares?q=         buscar desde dónde salir (calle, esquina, parque)
    POST /api/sesion             «6x1k» → sesión estructurada
    POST /api/ruta               sesión + salida → plan completo
    POST /api/ruta.gpx           lo mismo, en GPX
"""
from __future__ import annotations

import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from . import comunas, lugares
from .gpx import plan_a_gpx
from .sesion import Sesion, interpretar
from .planificador import planificar
from .series import SinTramo

WEB = Path(__file__).resolve().parents[1] / "web"

app = FastAPI(title="PacePath", version="0.1.0")
# Una ruta a la vez: el A* de RaceEngine reutiliza buffers por grafo (no es
# seguro entre hilos) y en 512 MB tampoco caben dos comunas cargando a la vez.
_motor = threading.Lock()
app.mount("/vendor", StaticFiles(directory=WEB / "vendor"), name="vendor")


class TextoSesion(BaseModel):
    texto: str = Field(min_length=1, max_length=120)


class PedidoRuta(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    texto: str | None = Field(default=None, max_length=120)
    sesion: Sesion | None = None
    comuna: str | None = None

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
    comuna = body.comuna or comunas.comuna_para(body.lat, body.lon)
    if comuna is None:
        raise HTTPException(422, "Ese punto no está en ninguna comuna con mapa todavía.")
    try:
        with _motor:
            ctx = comunas.contexto(comuna)
            return planificar(ctx, s, body.lat, body.lon)
    except SinTramo as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/ruta")
def ruta(body: PedidoRuta) -> dict:
    return _plan(body)


@app.post("/api/ruta.gpx")
def ruta_gpx(body: PedidoRuta) -> Response:
    plan = _plan(body)
    nombre = plan["sesion"]["resumen"].split(" (")[0].replace("×", "x").replace(" ", "")
    return Response(plan_a_gpx(plan), media_type="application/gpx+xml",
                    headers={"Content-Disposition": f'attachment; filename="pacepath-{nombre}.gpx"'})
