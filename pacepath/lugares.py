"""Buscar desde dónde sale el corredor: calles, esquinas y parques.

Usa la lista `lugares` que el workflow «Nombres de calle» publica por comuna,
así que funciona sin cargar ningún grafo y sin un geocodificador externo. Si no
encuentra nada, o si el texto trae número, pregunta a Nominatim (texto libre y,
si no, calle + comuna por separado) y después a Photon, otro buscador sobre
OpenStreetMap más tolerante con nombres incompletos. Antes se limpia lo que
confunde a los buscadores («depto 1203», «casa B», «#», «Santiago, Chile»).
En Chile pocas casas tienen su número en OpenStreetMap: si no hay número
exacto, se ofrece la calle, y si la dirección queda fuera de las comunas con
mapa, se dice (en vez de «sin resultados»).

    «pocuro»               → calles cuyo nombre lo contiene
    «pocuro 2500»          → la dirección (Nominatim), y la calle de respaldo
    «pocuro con tobalaba»  → la esquina (también «pocuro y tobalaba», «pocuro/tobalaba»)
"""
from __future__ import annotations

import difflib
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
        if not res and len(nq) >= 4:
            # Con una letra de más o de menos («apokindo», «pocuro»→«pocurro»):
            # lo más parecido, palabra por palabra.
            for norm, n, la, lo, t, c in indice():
                if t == "esquina":
                    continue
                # Sólo palabras que empiezan igual y de largo parecido (un error
                # de tipeo rara vez está en la primera letra): rápido sobre miles.
                cands = [w for w in [norm, *norm.split()] if w[:1] == nq[:1] and abs(len(w) - len(nq)) <= 3]
                if not cands:
                    continue
                r = max(difflib.SequenceMatcher(None, nq, w).ratio() for w in cands)
                if r >= 0.8:
                    res.append((10 - r * 5, len(n), n, la, lo, t, c))
    res.sort()
    return [{"nombre": n, "lat": la, "lon": lo, "tipo": t, "comuna": c}
            for _, _, n, la, lo, t, c in res[:limite]]


# Lo que viene después del número de casa y confunde a los buscadores.
_DEPTO = re.compile(r"\b(?:depto|dpto|departamento|dep|depa|casa|block|blk|torre|of|oficina|piso|local|"
                    r"apto|apartamento|interior|int|lote|parcela|sitio)\b\.?\s*[\w-]*", re.I)
_PAIS = re.compile(r",?\s*(?:regi[oó]n metropolitana|rm|santiago de chile|chile)\s*$", re.I)


def limpiar(texto: str) -> tuple[str, str | None]:
    """(calle y número, comuna o lugar si vino después de una coma)."""
    q = _PAIS.sub("", texto.strip())
    q = re.sub(r"\s+", " ", _DEPTO.sub(" ", q.replace("#", " ").replace("N°", " ").replace("n°", " "))).strip(" ,")
    calle, _, lugar = q.partition(",")
    lugar = lugar.strip(" ,") or None
    return calle.strip(), lugar


def _en_chile(lat: float, lon: float) -> bool:
    return -56.1 <= lat <= -17.4 and -76.0 <= lon <= -66.0


def buscar(texto: str, limite: int = 8, cerca: tuple[float, float] | None = None) -> list[dict]:
    """`cerca`: el centro del mapa. En Chile (o sin él) se busca como siempre,
    sesgado a las comunas con mapa; fuera, en todo el mundo alrededor de ahí."""
    if cerca and not _en_chile(*cerca):
        calle, lugar = limpiar(texto)
        if len(calle) < 3:
            return []
        q = f"{calle}, {lugar}" if lugar else calle
        return direcciones(q, limite, calle=calle, lugar=lugar, cerca=cerca)
    calle, lugar = limpiar(texto)
    if len(calle) < 3:
        return []
    q = f"{calle}, {lugar}" if lugar else calle
    if re.search(r"\d", calle):
        # Una dirección con número sólo la sabe ubicar el geocodificador; si no
        # la encuentra, al menos la calle (sin el número).
        out = direcciones(q, limite, calle=calle, lugar=lugar)
        sin_numero = re.sub(r"\s*(?:n[°º.]?\s*)?\d+\w?\b", "", calle).strip()
        if len(sin_numero) >= 3 and not any(r.get("exacta") for r in out):
            vistos = {r["nombre"] for r in out}
            out += [r for r in _locales(sin_numero, limite) if r["nombre"] not in vistos]
        return _ordenar(out)[:limite]
    return _locales(calle, limite) or _ordenar(direcciones(q, limite, calle=calle, lugar=lugar))[:limite]


def _ordenar(out: list[dict]) -> list[dict]:
    """Primero las que están en el mapa (las de fuera, al final y marcadas)."""
    return sorted(out, key=lambda r: bool(r.get("fuera")))


# ── Direcciones (Nominatim) ─────────────────────────────────────────────────
# Servicio público con política de uso: User-Agent propio y ≤ 1 petición por
# segundo. El teléfono espera a que se deje de escribir y aquí se guarda lo ya
# preguntado, así que una búsqueda cuesta una o dos peticiones.
NOMINATIM = "https://nominatim.openstreetmap.org/search"
_nom_lock = threading.Lock()
_nom_ultima = 0.0
_CACHE: dict[str, list[dict]] = {}


def _nominatim_http(params: dict) -> list[dict]:
    """Nominatim, respetando su política (1 petición por segundo, User-Agent propio)."""
    global _nom_ultima
    with _nom_lock:
        espera = 1.1 - (time.time() - _nom_ultima)
        if espera > 0:
            time.sleep(espera)
        _nom_ultima = time.time()
    req = urllib.request.Request(NOMINATIM + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "PacePath/0.1 (https://pacepathapp.com)"})
    from . import uso
    uso.contar_externa()
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
        return json.loads(r.read() or b"[]")


def _curadas() -> list[dict]:
    """Las comunas del catálogo (sin las celdas de la cuadrícula, que pueden estar
    en cualquier parte del mundo y estirarían la caja de búsqueda)."""
    return [c for c in comunas.disponibles() if c.get("grupo") != "Zonas"] or comunas.disponibles()


def _caja() -> str:
    """viewbox de Nominatim que cubre todas las comunas con grafo."""
    cs = _curadas()
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


def direcciones(q: str, limite: int = 8, calle: str | None = None, lugar: str | None = None,
                cerca: tuple[float, float] | None = None) -> list[dict]:
    """Chile primero (sesgado a las comunas con mapa); si nada, el mundo. Con
    `cerca` fuera de Chile, sólo el mundo, alrededor de ese punto."""
    mundo = cerca if cerca and not _en_chile(*cerca) else None
    clave = normalizar(q) + (f"@{round(mundo[0])},{round(mundo[1])}" if mundo else "")
    if clave in _CACHE:
        return _CACHE[clave][:limite]
    out: list[dict] = []
    intentos = []
    if not mundo:
        intentos.append(lambda: _nominatim({"q": q}))
        if calle and lugar:
            intentos.append(lambda: _nominatim({"street": calle, "city": lugar}))
        intentos.append(lambda: _photon(q))
    intentos += [lambda: _nominatim({"q": q}, mundo=mundo or True), lambda: _photon(q, mundo=mundo or True)]
    hubo_red = False
    for i, intento in enumerate(intentos):
        if out and i >= len(intentos) - 2 and not mundo:
            break                                 # en Chile hubo algo (aunque sin mapa): no buscar en el mundo
        try:
            nuevos = intento()
            hubo_red = True
        except Exception:  # noqa: BLE001 — sin red o servicio caído: el siguiente
            continue
        out = nuevos or out                       # si el siguiente no encuentra nada, se queda lo anterior
        if any(not r.get("fuera") for r in out):
            break
    numero = re.search(r"\b(\d+)\w?\b", calle or q)
    for r in out:
        r["exacta"] = bool(numero and re.search(rf"\b{numero.group(1)}\b", r["nombre"]))
    if hubo_red:                                   # sin red no se guarda: se reintenta
        if len(_CACHE) > 500:
            _CACHE.clear()
        _CACHE[clave] = out
    return out[:limite]


def _resultado(nombre: str, la: float, lo: float, lugar: str = "") -> dict:
    c = comunas.comuna_para(la, lo)
    r = {"nombre": nombre, "lat": la, "lon": lo, "tipo": "direccion", "comuna": c or lugar}
    if not c:
        r["fuera"] = True                         # existe, pero aún no hay mapa ahí
    return r


def _nominatim(params: dict, mundo: tuple[float, float] | bool | None = None) -> list[dict]:
    """En Chile, sesgado a las comunas con mapa; con `mundo`, en todas partes
    (alrededor del punto, si es un punto)."""
    base = {"format": "jsonv2", "addressdetails": 1, "limit": 8, "accept-language": "es,en"}
    if not mundo:
        base |= {"countrycodes": "cl", "viewbox": _caja(), "bounded": 0}
    elif isinstance(mundo, tuple):
        la, lo = mundo
        base |= {"viewbox": f"{lo - 0.5:.4f},{la + 0.5:.4f},{lo + 0.5:.4f},{la - 0.5:.4f}", "bounded": 0}
    datos = _nominatim_http(params | base)
    out, vistos = [], set()
    for e in datos if isinstance(datos, list) else []:
        nombre = _etiqueta(e)
        if nombre in vistos:
            continue
        vistos.add(nombre)
        a = e.get("address") or {}
        out.append(_resultado(nombre, float(e["lat"]), float(e["lon"]),
                              a.get("city") or a.get("town") or a.get("suburb") or ""))
    return out


PHOTON = "https://photon.komoot.io/api/"


def _photon(q: str, mundo: tuple[float, float] | bool | None = None) -> list[dict]:
    """Photon (komoot): busca sobre OpenStreetMap tolerando nombres a medias y
    errores; sesgado a Santiago y limitado a Chile, o con `mundo`, en todas
    partes (cerca del punto, si es un punto)."""
    if mundo:
        params = {"q": q, "limit": 8}
        if isinstance(mundo, tuple):
            params |= {"lat": f"{mundo[0]:.4f}", "lon": f"{mundo[1]:.4f}"}
    else:
        cs = _curadas()
        la0 = sum(c["lat"] for c in cs) / len(cs)
        lo0 = sum(c["lon"] for c in cs) / len(cs)
        params = {"q": q, "limit": 8, "lat": f"{la0:.4f}", "lon": f"{lo0:.4f}", "bbox": "-71.2,-34.0,-70.2,-33.0"}
    req = urllib.request.Request(PHOTON + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "PacePath/0.1 (https://pacepathapp.com)"})
    from . import uso
    uso.contar_externa()
    with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310
        datos = json.loads(r.read() or b"{}")
    out, vistos = [], set()
    for f in datos.get("features", []):
        p = f.get("properties") or {}
        if not mundo and p.get("countrycode", "CL").upper() != "CL":
            continue
        lo, la = f["geometry"]["coordinates"][:2]
        via = p.get("street") or p.get("name") or ""
        primero = f"{via} {p['housenumber']}" if via and p.get("housenumber") else (p.get("name") or via)
        lugar = p.get("district") or p.get("city") or p.get("county") or ""
        nombre = f"{primero}, {lugar}" if lugar and lugar not in primero else primero
        if not primero or nombre in vistos:
            continue
        vistos.add(nombre)
        out.append(_resultado(nombre, float(la), float(lo), lugar))
    return out