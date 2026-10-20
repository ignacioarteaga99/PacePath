"""Idioma de la petición en curso (español o inglés).

La API lo fija al empezar cada petición y los textos lo consultan con `tr`:

    tr(f"Las series van por {calle}", f"The intervals go along {calle}")

Es un ContextVar: cada petición tiene el suyo aunque corran en hilos distintos.
"""
from __future__ import annotations

from contextvars import ContextVar

_lang: ContextVar[str] = ContextVar("lang", default="es")


def usar(lang: str | None) -> None:
    _lang.set("en" if lang == "en" else "es")


def actual() -> str:
    return _lang.get()


def en() -> bool:
    return _lang.get() == "en"


def tr(es: str, en_: str) -> str:
    return en_ if _lang.get() == "en" else es
