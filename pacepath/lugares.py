"""Buscar desde dónde sale el corredor: calles, esquinas y parques.

Usa la lista `lugares` que el workflow «Nombres de calle» publica por comuna,
así que funciona sin cargar ningún grafo y sin un geocodificador externo. Si no
encuentra nada, o si el texto trae número, pregunta a Nominatim, que en el
servidor sí tiene red.

    «pocuro»               → calles cuyo nombre lo contiene
    «pocuro 2500»          → la dirección (Nominatim), y la calle de respaldo
    «pocuro con tobalaba»  → la esquina (también «pocuro y tobalaba», «pocuro/tobalaba»)
"""
from __future__ import annotations

import gzip
import json
import re
import threading
import time
import unicodedata
import urllib.parse
import urllib.request

from . import comunas
from .nombres import _descargar

_lock = threading.Lock()
_INDICE: list[tuple[str, str, float, float, str, str]] | None = None   # (norm, nombre, lat, lon, tipo, comuna)

_RELLENO = re.compile(r"\b(avenida|av|avda|calle|pasaje|psje|general|gral|presidente|pdte)\b\.?")
_SEP = re.compile(r"\s+(?:con|y|esquina|esq\.?)\s+|\s*[/&]\s*")


def normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _RELLENO.sub(" ", s)
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def _cargar_comuna(cid: str) -> list:
    f = _descargar(cid)
    if f is None:
        return []
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    if "lugares" not in data:
        # Archivo anterior a los lugares: se baja de nuevo una vez.
        f.unlink(missing_ok=True)
        f = _descargar(cid)
        if f is None:
            return []
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
    return [(n, la, lo, t, cid) for n, la, lo, t in data.get("lugares", [])]


def indice() -> list:
    global _INDICE
    with _lock:
        if _INDICE is None:
            filas, vistos = [], set()
            for c in comunas.disponibles():
                for n, la, lo, t, cid in _cargar_comuna(c["id"]):
                    # Los grafos se solapan en los bordes: la misma calle viene
                    # en dos comunas. Una sola entrada por nombre y tipo, salvo
                    # que estén lejos (calles homónimas en comunas distintas).
                    clave = (n, t, round(la, 2), round(lo, 2))
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    filas.append((normalizar(n), n, la, lo, t, cid))
            _INDICE = filas
        return _INDICE


def _puntaje(q: str, norm: str) -> int | None:
    if norm == q:
        return 0
    if norm.startswith(q):
        return 1
    if f" {q}" in f" {norm}":
        return 2
    if q in norm:
        return 3
    return None


def _locales(q: str, limite: int) -> list[dict]:
    partes = [normalizar(p) for p in _SEP.split(q.lower()) if p.strip()]
    res = []
    if len(partes) >= 2:
        a, b = partes[0], partes[1]
        for norm, n, la, lo, t, c in indice():
            if t != "esquina":
                continue
            x, _, y = norm.partition(" con ")
            for p, r in ((x, y), (y, x)):
                pa, pb = _puntaje(a, p), _puntaje(b, r)
                if pa is not None and pb is not None:
                    res.append((pa + pb, len(n), n, la, lo, t, c))
                    break
    else:
        nq = partes[0] if partes else ""
        orden_tipo = {"calle": 0, "parque": 0, "esquina": 5}
        for norm, n, la, lo, t, c in indice():
            if t == "esquina":
                continue
            p = _puntaje(nq, norm)
            if p is not None:
                res.append((p + orden_tipo[t], len(n), n, la, lo, t, c))
    res.sort()
    return [{"nombre": n, "lat": la, "lon": lo, "tipo": t, "comuna": c}
            for _, _, n, la, lo, t, c in res[:limite]]


def buscar(texto: str, limite: int = 8) -> list[dict]:
    q = texto.strip()
    if len(q) < 3:
        return []
    # «Pocuro 2500, Providencia»: la comuna después de la coma no es parte del nombre.
    calle = q.split(",")[0].strip()
    if re.search(r"\d", calle):
        # Una dirección con número sólo la sabe ubicar el geocodificador; si no
        # la encuentra, al menos la calle (sin el número).
        out = direcciones(q, limite)
        sin_numero = re.sub(r"\s*(?:#|n[°º.]?\s*)?\d+\w?\b", "", calle).strip()
        if len(sin_numero) >= 3:
            out += _locales(sin_numero, limite - len(out))
        return out[:limite]
    return _locales(calle, limite) or direcciones(q, limite)


# ── Direcciones (Nominatim) ─────────────────────────────────────────────────
# Servicio público con política de uso: User-Agent propio y ≤ 1 petición por
# segundo. El teléfono espera a que se deje de escribir y aquí se guarda lo ya
# preguntado, así que una búsqueda cuesta una o dos peticiones.
NOMINATIM = "https://nominatim.openstreetmap.org/search"
_nom_lock = threading.Lock()
_nom_ultima = 0.0
_CACHE: dict[str, list[dict]] = {}


def _nominatim_http(params: dict) -> list[dict]:
    global _nom_ultima
    with _nom_lock:
        espera = 1.1 - (time.time() - _nom_ultima)
        if espera > 0:
            time.sleep(espera)
        _nom_ultima = time.time()
    req = urllib.request.Request(NOMINATIM + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "PacePath/0.1 (https://pacepath.onrender.com)"})
    from . import uso
    uso.contar_externa()
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
        return json.loads(r.read() or b"[]")


def _caja() -> str:
    """viewbox de Nominatim que cubre todas las comunas con grafo."""
    cs = comunas.disponibles()
    k = 111_195.0
    oeste = min(c["lon"] - c["radio_m"] / (k * 0.83) for c in cs)
    este = max(c["lon"] + c["radio_m"] / (k * 0.83) for c in cs)
    sur = min(c["lat"] - c["radio_m"] / k for c in cs)
    norte = max(c["lat"] + c["radio_m"] / k for c in cs)
    return f"{oeste:.4f},{norte:.4f},{este:.4f},{sur:.4f}"


def _etiqueta(e: dict) -> str:
    a = e.get("address") or {}
    via = a.get("road") or a.get("pedestrian") or a.get("footway") or ""
    primero = (f"{via} {a['house_number']}" if via and a.get("house_number") else via) \
        or e.get("name") or (e.get("display_name") or "").split(",")[0]
    lugar = a.get("suburb") or a.get("city_district") or a.get("city") or a.get("town") or ""
    return f"{primero}, {lugar}" if lugar and lugar not in primero else primero


def direcciones(q: str, limite: int = 8) -> list[dict]:
    clave = normalizar(q)
    if clave in _CACHE:
        return _CACHE[clave][:limite]
    try:
        datos = _nominatim_http({
            "q": q, "format": "jsonv2", "addressdetails": 1, "limit": min(limite, 10),
            "countrycodes": "cl", "viewbox": _caja(), "bounded": 1, "accept-language": "es",
        })
    except Exception:  # noqa: BLE001 — sin red, sin resultados (no se guarda)
        return []
    out, vistos = [], set()
    for e in datos if isinstance(datos, list) else []:
        la, lo = float(e["lat"]), float(e["lon"])
        c = comunas.comuna_para(la, lo)
        nombre = _etiqueta(e)
        if c and nombre not in vistos:
            vistos.add(nombre)
            out.append({"nombre": nombre, "lat": la, "lon": lo, "tipo": "direccion", "comuna": c})
    if len(_CACHE) > 500:
        _CACHE.clear()
    _CACHE[clave] = out
    return out[:limite]
