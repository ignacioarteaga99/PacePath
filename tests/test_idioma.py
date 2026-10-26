"""Español, inglés y portugués (Brasil)."""
from fastapi.testclient import TestClient

from pacepath import idioma
from pacepath.api import app
from pacepath.sesion import interpretar

cli = TestClient(app)


def test_normalizar_y_respaldo_en_ingles():
    assert [idioma.normalizar(x) for x in ("pt-BR", "PT", "en-US", "fr", None)] == ["pt", "pt", "en", "es", "es"]
    idioma.usar("pt")
    try:
        assert idioma.tr("hola", "hi", "olá") == "olá"
        assert idioma.tr("hola", "hi") == "hi"            # sin portugués: inglés
    finally:
        idioma.usar("es")


def test_sesiones_en_portugues():
    idioma.usar("pt")
    try:
        assert interpretar("longão 18k plano").resumen == "longão 18 km (máx. +90 m)"
        assert interpretar("tiros 6x1k").tipo.value == "series"
        assert interpretar("leve 45 min").tipo.value == "regenerativo"
        assert interpretar("longo 10 milhas").distancia_m > 16_000
    finally:
        idioma.usar("es")


def test_la_api_responde_en_portugues():
    r = cli.post("/api/sesion", json={"texto": "xx"}, headers={"X-Idioma": "pt"})
    assert r.status_code == 422 and r.json()["detail"].startswith("Não entendi")
    r = cli.post("/api/sesion", json={"texto": "xx"}, headers={"X-Idioma": "es"})
    assert r.json()["detail"].startswith("No entiendo")
