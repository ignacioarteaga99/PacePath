import pytest

from pacepath.sesion import Sesion, TipoSesion, interpretar, requisitos


@pytest.mark.parametrize("texto,reps,dist,pausa", [
    ("6x1k", 6, 1000, 90),
    ("6×1000", 6, 1000, 90),
    ("8x400 r60", 8, 400, 60),
    ("series 5 x 2km p2min", 5, 2000, 120),
    ("10x200m", 10, 200, 90),
    ("4x1,5k", 4, 1500, 90),
])
def test_interpreta_series(texto, reps, dist, pausa):
    s = interpretar(texto)
    assert s.tipo is TipoSesion.SERIES
    assert (s.repeticiones, s.distancia_rep_m, s.pausa_s) == (reps, dist, pausa)


def test_interpreta_otras():
    assert interpretar("cuestas 8x300 6%").tipo is TipoSesion.CUESTAS
    assert interpretar("cuestas 8x300 6%").pendiente_pct == 6
    f = interpretar("fondo 18k")
    assert f.tipo is TipoSesion.FONDO and f.distancia_m == 18000
    assert interpretar("tempo 8").distancia_m == 8000
    assert interpretar("regenerativo 6k").tipo is TipoSesion.REGENERATIVO


def test_no_entiende():
    with pytest.raises(ValueError, match="no entiendo"):
        interpretar("hoy descanso")


def test_resumen():
    assert interpretar("6x1k").resumen == "6×1 km (pausa 90 s)"
    assert interpretar("8x400 r60").resumen == "8×400 m (pausa 60 s)"


def test_series_exigen_repeticiones():
    with pytest.raises(ValueError):
        Sesion(tipo=TipoSesion.SERIES, distancia_m=5000)


def test_requisitos_series():
    r = requisitos(interpretar("6x1k"))
    assert r.largo_tramo_m == 1000
    assert r.max_semaforos == 0 and r.max_cruces_mayores == 0
    assert r.max_desnivel_tramo_m == pytest.approx(12)
    # Repeticiones largas: se exige menos rectitud, se tolera más desnivel.
    largo = requisitos(interpretar("3x2k"))
    assert largo.min_rectitud < r.min_rectitud
    assert largo.max_desnivel_tramo_m > r.max_desnivel_tramo_m


def test_requisitos_por_tipo():
    from pacepath.sesion import TipoSesion
    c = requisitos(interpretar("cuestas 8x300 6%"))
    assert c.pendiente_objetivo_pct == 6 and c.max_cruces_menores == 0
    t = requisitos(interpretar("tempo 8k"))
    f = requisitos(interpretar("fondo 18k"))
    r = requisitos(interpretar("regenerativo 6k"))
    # El tempo es el que más castiga detenerse; el regenerativo, subir.
    assert t.penal_semaforo_m > f.penal_semaforo_m > r.penal_semaforo_m
    assert r.peso_subida > f.peso_subida
    assert f.radio_agua_m > 0 and f.tipo is TipoSesion.FONDO
