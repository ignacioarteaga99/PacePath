"""Buscador de la salida: calles locales y direcciones con número (Nominatim simulado)."""
from pacepath import comunas, lugares

PROV = {"id": "providencia", "nombre": "Providencia", "lat": -33.43, "lon": -70.61, "radio_m": 6000, "mb": 1}
INDICE = [(lugares.normalizar(n), n, la, lo, t, "providencia") for n, la, lo, t in [
    ("Avenida Pocuro", -33.433, -70.600, "calle"),
    ("Avenida Pocuro con Avenida Tobalaba", -33.433, -70.598, "esquina"),
]]


def _preparar(monkeypatch, respuesta):
    pedidos = []
    monkeypatch.setattr(lugares, "indice", lambda: INDICE)
    monkeypatch.setattr(comunas, "disponibles", lambda: [PROV])
    monkeypatch.setattr(lugares, "_CACHE", {})

    def http(params):
        pedidos.append(params)
        return respuesta
    monkeypatch.setattr(lugares, "_nominatim_http", http)
    return pedidos


def test_direccion_con_numero_va_primero(monkeypatch):
    pedidos = _preparar(monkeypatch, [{
        "lat": "-33.4339", "lon": "-70.6041", "name": "",
        "address": {"road": "Avenida Pocuro", "house_number": "2500", "suburb": "Providencia"}}])
    r = lugares.buscar("Pocuro 2500, Providencia")
    assert r[0] == {"nombre": "Avenida Pocuro 2500, Providencia", "lat": -33.4339, "lon": -70.6041,
                    "tipo": "direccion", "comuna": "providencia"}
    assert r[1]["nombre"] == "Avenida Pocuro"               # la calle, de respaldo
    p = pedidos[0]
    assert p["countrycodes"] == "cl" and p["bounded"] == 1 and p["viewbox"]
    lugares.buscar("Pocuro 2500, Providencia")
    assert len(pedidos) == 1                                 # la segunda vez, de la caché


def test_sin_numero_no_pregunta_a_nominatim(monkeypatch):
    pedidos = _preparar(monkeypatch, [])
    assert lugares.buscar("pocuro")[0]["nombre"] == "Avenida Pocuro"
    assert lugares.buscar("pocuro con tobalaba")[0]["tipo"] == "esquina"
    assert pedidos == []


def test_direccion_fuera_de_las_comunas_se_descarta(monkeypatch):
    _preparar(monkeypatch, [{"lat": "-33.60", "lon": "-70.90", "address": {"road": "Lejos", "house_number": "1"}}])
    assert lugares.buscar("Lejos 1") == []


def test_sin_red_no_falla(monkeypatch):
    _preparar(monkeypatch, [])

    def falla(params):
        raise OSError("sin red")
    monkeypatch.setattr(lugares, "_nominatim_http", falla)
    assert [x["nombre"] for x in lugares.buscar("Pocuro 2500")] == ["Avenida Pocuro"]
