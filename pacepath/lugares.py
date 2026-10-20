"""Buscar desde dónde sale el corredor: calles, esquinas y parques.

Usa la lista `lugares` que el workflow «Nombres de calle» publica por comuna,
así que funciona sin cargar ningún grafo y sin un geocodificador externo. Si no
encuentra nada, prueba con Nominatim (vía RaceEngine), que en el servidor sí
tiene red.

    «pocuro»               → calles cuyo nombre lo contiene
    «pocuro con tobalaba»  → la esquina (también «pocuro y tobalaba», «pocuro/tobalaba»)
"""
from __future__ import annotations

import gzip
import json
import re
import threading
import unicodedata

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


def buscar(texto: str, limite: int = 8) -> list[dict]:
    q = texto.strip()
    if len(q) < 3:
        return []
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
    out = [{"nombre": n, "lat": la, "lon": lo, "tipo": t, "comuna": c}
           for _, _, n, la, lo, t, c in res[:limite]]
    if not out:
        out = _nominatim(q, limite)
    return out


def _nominatim(q: str, limite: int) -> list[dict]:
    try:
        from raceengine_geodata.geocode import search
        r = search(f"{q}, Santiago, Chile", limit=limite, timeout=8.0)
    except Exception:  # noqa: BLE001 — sin red, sin resultados
        return []
    out = []
    for e in r:
        c = comunas.comuna_para(float(e["lat"]), float(e["lon"]))
        if c:
            out.append({"nombre": e.get("name") or q, "lat": float(e["lat"]),
                        "lon": float(e["lon"]), "tipo": "direccion", "comuna": c})
    return out
