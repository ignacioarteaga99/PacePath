import os

import pytest

# Los tests sin red no deben esperar a GitHub buscando nombres de calle.
os.environ.setdefault("PACEPATH_NOMBRES_URL", "")
# Sin cuenta obligatoria (las cuentas y la beta se prueban en test_acceso.py).
os.environ.setdefault("PACEPATH_ACCESO_LIBRE", "1")

import pacepath  # noqa: E402,F401 — fija las variables de entorno de RaceEngine


def pytest_collection_modifyitems(config, items):
    """Los tests `real` descargan un grafo de comuna (~9 MB): se saltan con
    PACEPATH_SIN_RED=1."""
    if os.environ.get("PACEPATH_SIN_RED") == "1":
        salta = pytest.mark.skip(reason="PACEPATH_SIN_RED=1")
        for it in items:
            if "real" in it.keywords:
                it.add_marker(salta)


@pytest.fixture(autouse=True, scope="session")
def _registro_temporal(tmp_path_factory):
    """El registro de consultas de los tests va a una SQLite propia, no a la de /tmp."""
    from pacepath import registro
    registro.configurar(str(tmp_path_factory.mktemp("registro") / "registro.sqlite"))
    # La beta cerrada (usuario y clave) se prueba aparte, en test_acceso.py.
    from pacepath import acceso
    acceso.olvidar()
    acceso.configurar(activo=False)


@pytest.fixture(autouse=True)
def _sin_rutas_recientes():
    """Cada test calcula sus rutas: nada de la caché de otro test."""
    from pacepath import recientes
    recientes.olvidar()
    yield
