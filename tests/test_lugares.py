"""Buscador de la salida: calles locales y direcciones con número (Nominatim y Photon simulados)."""
from pacepath import comunas, lugares

PROV = {"id": "providencia", "nombre": "Providencia", "lat": -33.43, "lon": -70.61, "radio_m": 6000, "mb": 1}
INDICE = [(lugares.normalizar(n), n, la, lo, t, "providencia") for n, la, lo, t in [
    ("Avenida Pocuro", -33.433, -70.600, "calle"),
    ("Avenida Pocuro con Avenida Tobalaba", -33.433, -70.598, "esquina"),
]]


def _preparar(monkeypatch, respuesta, photon=None):
    pedidos = []
    monkeypatch.setattr(lugares, "indice", lambda: INDICE)
    monkeypatch.setattr(comunas, "disponibles", lambda: [PROV])
    monkeypatch.setattr(lugares, "_CACHE", {})

    def http(params):
        pedidos.append(params)
        return respuesta(params) if callable(respuesta) else respuesta
    monkeypatch.setattr(lugares, "_nominatim_http", http)
    monkeypatch.setattr(lugares, "_photon", lambda q: pedidos.append({"photon": q}) or [
        lugares._resultado(*x) for x in (photon or [])])
    return pedidos


def test_direccion_con_numero_va_primero(monkeypatch):
    pedidos = _preparar(monkeypatch, [{
        "lat": "-33.4339", "lon": "-70.6041", "name": "",
        "address": {"road": "Avenida Pocuro", "house_number": "2500", "suburb": "Providencia"}}])
    r = lugares.buscar("Pocuro 2500, Providencia")
    assert r[0] == {"nombre": "Avenida Pocuro 2500, Providencia", "lat": -33.4339, "lon": -70.6041,
                    "tipo": "direccion", "comuna": "providencia", "exacta": True}
    assert len(r) == 1                                       # con el número exacto, la calle sola sobra
    p = pedidos[0]
    assert p["countrycodes"] == "cl" and p["viewbox"]
    lugares.buscar("Pocuro 2500, Providencia")
    assert len(pedidos) == 1                                 # la segunda vez, de la caché


def test_sin_numero_no_pregunta_a_nominatim(monkeypatch):
    pedidos = _preparar(monkeypatch, [])
    assert lugares.buscar("pocuro")[0]["nombre"] == "Avenida Pocuro"
    assert lugares.buscar("pocuro con tobalaba")[0]["tipo"] == "esquina"
    assert pedidos == []


def test_direccion_fuera_de_las_comunas_se_avisa(monkeypatch):
    _preparar(monkeypatch, [{"lat": "-33.60", "lon": "-70.90", "address": {"road": "Lejos", "house_number": "1",
                                                                           "city": "Talagante"}}])
    r = lugares.buscar("Lejos 1")
    assert r[0]["fuera"] and r[0]["comuna"] == "Talagante"     # existe, pero aún sin mapa: se dice


def test_sin_red_no_falla(monkeypatch):
    _preparar(monkeypatch, [])

    def falla(params):
        raise OSError("sin red")
    monkeypatch.setattr(lugares, "_nominatim_http", falla)
    assert [x["nombre"] for x in lugares.buscar("Pocuro 2500")] == ["Avenida Pocuro"]


def test_limpia_lo_que_confunde_al_buscador():
    assert lugares.limpiar("Av. Apoquindo 4500 depto 1203, Las Condes, Chile") == ("Av. Apoquindo 4500", "Las Condes")
    assert lugares.limpiar("Pocuro #2500 casa B") == ("Pocuro 2500", None)
    assert lugares.limpiar("Los Leones 1300 dpto. 52, Providencia, Región Metropolitana") == ("Los Leones 1300", "Providencia")


def test_si_nominatim_no_sabe_el_numero_prueba_calle_y_comuna_y_photon(monkeypatch):
    # Texto libre: nada. Calle + comuna: nada. Photon: la casa.
    pedidos = _preparar(monkeypatch, [], photon=[("Avenida Pocuro 2510, Providencia", -33.434, -70.603)])
    r = lugares.buscar("Pocuro 2510 depto 3, Providencia")
    assert pedidos[0]["q"] == "Pocuro 2510, Providencia"               # sin el depto
    assert pedidos[1]["street"] == "Pocuro 2510" and pedidos[1]["city"] == "Providencia"
    assert pedidos[2] == {"photon": "Pocuro 2510, Providencia"}
    assert r[0]["nombre"] == "Avenida Pocuro 2510, Providencia" and r[0]["exacta"]


def test_sin_numero_exacto_ofrece_la_calle(monkeypatch):
    _preparar(monkeypatch, [], photon=[("Avenida Pocuro, Providencia", -33.433, -70.600)])
    r = lugares.buscar("Pocuro 9999")
    assert not r[0]["exacta"] and [x["nombre"] for x in r][-1] == "Avenida Pocuro"


def test_tolera_errores_de_tipeo(monkeypatch):
    pedidos = _preparar(monkeypatch, [])
    assert lugares.buscar("pocurro")[0]["nombre"] == "Avenida Pocuro"
    assert lugares.buscar("pocro")[0]["nombre"] == "Avenida Pocuro"
    assert not [p for p in pedidos if "q" in p and p["q"] == "pocurro"]       # lo resolvió sin salir
