import os

import pytest

# Los tests sin red no deben esperar a GitHub buscando nombres de calle.
os.environ.setdefault("PACEPATH_NOMBRES_URL", "")

import pacepath  # noqa: E402,F401 — fija las variables de entorno de RaceEngine


def pytest_collection_modifyitems(config, items):
    """Los tests `real` descargan un grafo de comuna (~9 MB): se saltan con
    PACEPATH_SIN_RED=1."""
    if os.environ.get("PACEPATH_SIN_RED") == "1":
        salta = pytest.mark.skip(reason="PACEPATH_SIN_RED=1")
        for it in items:
            if "real" in it.keywords:
                it.add_marker(salta)
