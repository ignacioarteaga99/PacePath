"""Idioma de la petición en curso (español, inglés o portugués de Brasil).

La API lo fija al empezar cada petición y los textos lo consultan con `tr`:

    tr(f"Las series van por {calle}", f"The intervals go along {calle}", f"Os tiros vão pela {calle}")

Sin el portugués, en portugués se muestra el inglés.

Es un ContextVar: cada petición tiene el suyo aunque corran en hilos distintos.
"""
from __future__ import annotations

from contextvars import ContextVar

_lang: ContextVar[str] = ContextVar("lang", default="es")


IDIOMAS = ("es", "en", "pt")


def normalizar(lang: str | None) -> str:
    """«en», «en-US», «pt-BR», «pt»… → es / en / pt (lo demás, español)."""
    lang = (lang or "").strip().lower()[:2]
    return lang if lang in IDIOMAS else "es"


def usar(lang: str | None) -> None:
    _lang.set(normalizar(lang))


def actual() -> str:
    return _lang.get()


def en() -> bool:
    return _lang.get() == "en"


def pt() -> bool:
    return _lang.get() == "pt"


def tr(es: str, en_: str, pt_: str | None = None) -> str:
    lang = _lang.get()
    if lang == "pt":
        return pt_ if pt_ is not None else en_
    return en_ if lang == "en" else es
