"""Caché en disco de lo que se deriva del grafo de cada comuna (nombres,
parques, trazos para las series).

Con una comuna en memoria a la vez (512 MB), quien pide una ruta en otra
comuna obliga a recalcular todo: ~4 s en Santiago Centro. Guardado en disco,
volver a una comuna ya visitada lo lee en ~0,15 s. En Render el disco dura lo
que dure la instancia (en Starter no se duerme); si se borra, se recalcula.

La clave lleva la versión del grafo, la del archivo de nombres (de él salen
los parques) y `CODIGO`: súbelo al cambiar cómo se calcula algo de aquí.
"""
from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Callable, TypeVar

from raceengine_engine.context import GraphContext

CODIGO = "1"
_DIR = Path(os.environ.get("RACEENGINE_DATA", "data")) / "derivados"
T = TypeVar("T")


def _huella(ctx: GraphContext) -> str:
    f = Path(os.environ.get("RACEENGINE_DATA", "data")) / "nombres" / f"{ctx.region_id}.nombres.json.gz"
    try:
        st = f.stat()
        nombres = f"{st.st_size}-{int(st.st_mtime)}"
    except OSError:
        nombres = "sin"
    return f"{ctx.version}-{nombres}-{CODIGO}"


def en_disco(nombre: str, ctx: GraphContext, calcular: Callable[[], T]) -> T:
    ruta = _DIR / f"{ctx.region_id}-{nombre}-{_huella(ctx)}.pkl"
    try:
        with open(ruta, "rb") as fh:
            return pickle.load(fh)
    except Exception:  # noqa: BLE001 — no está, o se cortó a medio escribir: recalcular
        pass
    valor = calcular()
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        for viejo in ruta.parent.glob(f"{ctx.region_id}-{nombre}-*.pkl"):
            viejo.unlink(missing_ok=True)          # versiones anteriores de lo mismo
        tmp = ruta.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(valor, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, ruta)
    except OSError as exc:
        print(f"[disco] no pude guardar {ruta.name}: {exc}", flush=True)
    return valor
