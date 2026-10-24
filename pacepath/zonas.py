"""«Preparando tu zona»: mapas a pedido en cualquier parte del mundo.

Donde no hay mapa, la ruta no se rechaza: se pide construir la celda de la
cuadrícula mundial de RaceEngine (`raceengine_geodata.grid`, ~8 km de lado)
que contiene la salida. El servidor no puede construirla (necesita 0,5–1,5 GB
de RAM): lanza el workflow «Construir región» de RaceEngine en GitHub Actions,
que la publica en la Release `graphs` en unos minutos. El teléfono pregunta
cada tanto (`/api/zona/{id}`) y vuelve a pedir la ruta cuando está; quien
tiene cuenta recibe además un correo. Después se lanzan los nombres de calle
de la zona (workflow «Nombres de calle» de PacePath).

Lo pedido queda en la tabla `pp_zonas` (Render borra el disco y se duerme):
al despertar se retoma lo que estaba en camino.

Variables en Render:
  PACEPATH_GITHUB_TOKEN   token «fine-grained» con Actions: Read and write en
                          RaceEngine y PacePath. Sin él, todo sigue como antes
                          (fuera del mapa se avisa que no hay mapa).
  PACEPATH_RAMA           rama de PacePath con el workflow de nombres
                          (por defecto fase-0-1-series).
  PACEPATH_ZONAS_DIA      tope de zonas nuevas por día (40), para que nadie
                          llene la cola; cada cuenta, hasta 5.

Una ejecución del workflow construye varias celdas, y GitHub deja una sola
esperando por grupo de concurrencia (las anteriores en espera se cancelan):
por eso cada envío lleva todas las pedidas que faltan, y una pedida que no
aparece en 15 minutos se vuelve a enviar. A los 60 sin aparecer, falló (mar,
desierto, sin calles).
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from raceengine_geodata import grid, remote

from . import ciudades, correo, cuentas, registro, uso

API = "https://api.github.com/repos"
MOTOR = os.environ.get("PACEPATH_MOTOR_REPO", "ignacioarteaga99/RaceEngine")
APP = os.environ.get("PACEPATH_APP_REPO", "ignacioarteaga99/PacePath")
ESPERA_S = 300                  # lo que suele tardar (para la barra del teléfono)
REENVIAR_S = 15 * 60
FALLA_S = 60 * 60
POR_CUENTA = 5
LOTE = 20

_lock = threading.Lock()
_vigilando = False
_mirado = 0.0


def activo() -> bool:
    return bool(os.environ.get("PACEPATH_GITHUB_TOKEN", "").strip())


def _ts(t: datetime | None = None) -> str:
    return (t or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _segundos(desde: str | None) -> float:
    if not desde:
        return 1e9
    return (datetime.now(timezone.utc) - datetime.strptime(desde, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)).total_seconds()


def _fila(zid: str) -> dict | None:
    f = registro.sql("SELECT id, nombre, lat, lon, estado, pedida, enviada, lista, cuenta, avisar "
                     "FROM pp_zonas WHERE id = ?", (zid,), traer=True)
    if not f:
        return None
    return dict(zip(("id", "nombre", "lat", "lon", "estado", "pedida", "enviada", "lista", "cuenta", "avisar"), f[0]))


def _nombre(lat: float, lon: float) -> str:
    """Un nombre para mostrar mientras se prepara (el definitivo lo pone el workflow)."""
    try:
        lugar = ciudades.nombrar(lat, lon) or {}
    except Exception:  # noqa: BLE001
        lugar = {}
    return lugar.get("ciudad") or lugar.get("barrio") or f"{lat:.2f}, {lon:.2f}"


def _manifiesto(fresco: bool = False) -> dict:
    """El manifiesto de grafos; `fresco` lo vuelve a bajar (a lo más cada 20 s)."""
    global _mirado
    if fresco and time.time() - _mirado > 20:
        _mirado = time.time()
        return remote.manifest(force=True)
    return remote.manifest()


def _github(repo: str, workflow: str, ref: str, inputs: dict) -> bool:
    req = urllib.request.Request(
        f"{API}/{repo}/actions/workflows/{workflow}/dispatches", method="POST",
        data=json.dumps({"ref": ref, "inputs": inputs}).encode(),
        headers={"Authorization": f"Bearer {os.environ['PACEPATH_GITHUB_TOKEN'].strip()}",
                 "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "PacePath/0.1 (https://pacepathapp.com)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
            return r.status == 204
    except Exception as exc:  # noqa: BLE001
        print(f"[zonas] no pude lanzar {repo}/{workflow}: {exc}", flush=True)
        return False


def _enviar_pendientes() -> bool:
    """Lanza la construcción de todas las pedidas que faltan (ver arriba por qué todas)."""
    filas = registro.sql("SELECT id FROM pp_zonas WHERE estado = 'pedida' ORDER BY pedida LIMIT ?",
                         (LOTE,), traer=True)
    ids = [f[0] for f in filas]
    if not ids:
        return False
    ok = _github(MOTOR, "build-region.yml", "main", {"region": ",".join(ids), "elevation": "copernicus"})
    if ok:
        registro.sql(f"UPDATE pp_zonas SET enviada = ? WHERE id IN ({','.join('?' * len(ids))})",
                     (_ts(), *ids))
    return ok


def _publico(z: dict, man: dict | None = None) -> dict:
    e = (man or {}).get(z["id"]) or {}
    lleva = max(0, round(_segundos(z["pedida"])))
    return {"id": z["id"], "estado": z["estado"], "nombre": e.get("label") or z["nombre"],
            "lat": z["lat"], "lon": z["lon"], "lleva_s": lleva, "espera_s": ESPERA_S}


def pedir(lat: float, lon: float) -> dict:
    """La celda de la salida: si ya está, «lista»; si no, se pide (o se suma a
    quienes la esperan). Estados: lista · pedida · fallida · limite · no_disponible."""
    zid = grid.celda(lat, lon)
    if not activo():
        return {"id": zid, "estado": "no_disponible"}
    cuenta = (uso.consulta.get() or {}).get("cuenta")
    man = _manifiesto(fresco=True)
    with _lock:
        z = _fila(zid)
        if zid in man:
            if z and z["estado"] != "lista":
                _marcar_lista(z, man)
            return {"id": zid, "estado": "lista", "nombre": man[zid].get("label", zid)}
        if z and z["estado"] == "pedida":
            if cuenta and cuenta not in (z["avisar"] or "").split(","):
                avisar = ",".join(x for x in ((z["avisar"] or "").split(",") + [cuenta]) if x)
                registro.sql("UPDATE pp_zonas SET avisar = ? WHERE id = ?", (avisar, zid))
            _vigilar()
            return _publico(z)
        if z and z["estado"] == "fallida" and _segundos(z["pedida"]) < 86400:
            return _publico(z)
        hoy = _ts(datetime.now(timezone.utc) - timedelta(days=1))
        total, mias = registro.sql(
            "SELECT COUNT(*), SUM(CASE WHEN cuenta = ? THEN 1 ELSE 0 END) FROM pp_zonas WHERE pedida >= ?",
            (cuenta or "", hoy), traer=True)[0]
        if total >= int(os.environ.get("PACEPATH_ZONAS_DIA", "40")) or (cuenta and (mias or 0) >= POR_CUENTA):
            return {"id": zid, "estado": "limite"}
        nueva = {"id": zid, "nombre": _nombre(lat, lon), "lat": round(lat, 5), "lon": round(lon, 5),
                 "estado": "pedida", "pedida": _ts(), "cuenta": cuenta, "avisar": cuenta or ""}
        registro.sql("DELETE FROM pp_zonas WHERE id = ?", (zid,))
        registro.sql("INSERT INTO pp_zonas (id, nombre, lat, lon, estado, pedida, cuenta, avisar) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     tuple(nueva[k] for k in ("id", "nombre", "lat", "lon", "estado", "pedida", "cuenta", "avisar")))
        uso.registrar("zona_pedida", nueva["nombre"], comuna=zid)
        _enviar_pendientes()
        _vigilar()
        return _publico(nueva | {"enviada": None, "lista": None})


def estado(zid: str) -> dict:
    """Para el teléfono que espera: revisa si ya apareció en el manifiesto."""
    if not grid.es_celda(zid):
        return {"id": zid, "estado": "desconocida"}
    man = _manifiesto(fresco=True)
    with _lock:
        z = _fila(zid)
        if zid in man:
            if z and z["estado"] != "lista":
                _marcar_lista(z, man)
            return {"id": zid, "estado": "lista", "nombre": man[zid].get("label", zid)}
        if not z:
            return {"id": zid, "estado": "desconocida"}
        _revisar_tiempos(z)
        return _publico(_fila(zid) or z, man)


def _revisar_tiempos(z: dict) -> None:
    if z["estado"] != "pedida":
        return
    if _segundos(z["pedida"]) > FALLA_S:
        registro.sql("UPDATE pp_zonas SET estado = 'fallida' WHERE id = ?", (z["id"],))
        uso.registrar("zona_fallida", z["nombre"], comuna=z["id"], exito=False)
    elif _segundos(z["enviada"]) > REENVIAR_S:
        _enviar_pendientes()


def _marcar_lista(z: dict, man: dict) -> None:
    """Apareció: se anota, se avisa por correo a quienes la esperaban y se
    lanzan sus nombres de calle."""
    registro.sql("UPDATE pp_zonas SET estado = 'lista', lista = ? WHERE id = ?", (_ts(), z["id"]))
    nombre = man.get(z["id"], {}).get("label") or z["nombre"]
    uso.registrar("zona_lista", nombre, f"{round(_segundos(z['pedida']) / 60)} min", comuna=z["id"])
    _github(APP, "nombres.yml", os.environ.get("PACEPATH_RAMA", "fase-0-1-series"), {"comunas": z["id"]})
    if not correo.activo():
        return
    for uid in {x for x in (z["avisar"] or "").split(",") if x}:
        u = cuentas.por_id(uid)
        if u and u.get("activo"):
            correo.en_fondo(correo.zona_lista, u["correo"], u.get("nombre"), nombre, z["lat"], z["lon"],
                            lang=u.get("idioma"))


def revisar() -> int:
    """Una pasada sobre lo pedido (la hace el hilo vigilante). Devuelve cuántas faltan."""
    filas = registro.sql("SELECT id FROM pp_zonas WHERE estado = 'pedida'", traer=True)
    if not filas:
        return 0
    man = _manifiesto(fresco=True)
    with _lock:
        for (zid,) in filas:
            z = _fila(zid)
            if not z or z["estado"] != "pedida":
                continue
            if zid in man:
                _marcar_lista(z, man)
            else:
                _revisar_tiempos(z)
    return len(registro.sql("SELECT id FROM pp_zonas WHERE estado = 'pedida'", traer=True))


def _vigilar() -> None:
    """Un hilo que mira cada minuto mientras haya zonas en camino (para avisar
    por correo aunque el teléfono ya no esté preguntando)."""
    global _vigilando
    if _vigilando or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    _vigilando = True

    def bucle():
        global _vigilando
        try:
            while True:
                time.sleep(60)
                try:
                    if revisar() == 0:
                        return
                except Exception as exc:  # noqa: BLE001
                    print(f"[zonas] vigilante: {type(exc).__name__}: {exc}", flush=True)
        finally:
            _vigilando = False

    threading.Thread(target=bucle, name="zonas", daemon=True).start()


def reanudar() -> None:
    """Al despertar: si quedó algo en camino, seguir mirándolo."""
    if not activo():
        return
    try:
        if registro.sql("SELECT id FROM pp_zonas WHERE estado = 'pedida' LIMIT 1", traer=True):
            _vigilar()
    except Exception as exc:  # noqa: BLE001
        print(f"[zonas] no pude reanudar: {exc}", flush=True)
