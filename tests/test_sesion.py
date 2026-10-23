import pytest

from pacepath.sesion import Sesion, TipoSesion, interpretar, requisitos


@pytest.mark.parametrize("texto,reps,dist,pausa", [
    ("6x1k", 6, 1000, None),          # sin pausa escrita, no se inventa
    ("6×1000", 6, 1000, None),
    ("8x400 r60", 8, 400, 60),
    ("series 5 x 2km p2min", 5, 2000, 120),
    ("10x200m", 10, 200, None),
    ("4x1,5k", 4, 1500, None),
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
    with pytest.raises(ValueError, match="(?i)no entiendo"):
        interpretar("hoy descanso")


def test_resumen():
    assert interpretar("6x1k").resumen == "6×1 km"
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


@pytest.mark.parametrize("texto,tope", [
    ("fondo 18k +100m", 100), ("fondo 18k desnivel 100", 100), ("regenerativo 6k d+40", 40),
    ("tempo 8k max 60m", 60), ("fondo 18k subida máx de 120 m", 120), ("fondo 18k", None),
])
def test_tope_de_desnivel(texto, tope):
    s = interpretar(texto)
    assert s.max_subida_m == tope
    assert s.distancia_m in (18000, 6000, 8000)          # el tope no se confunde con la distancia
    r = requisitos(s)
    if tope is not None:
        assert r.max_subida_m == tope and r.penal_subida_m_por_m >= 8


def test_desnivel_con_umbral():
    from pacepath.metricas import desnivel
    # Una bajadita de 1 m (ruido) no corta la subida ni suma de más.
    assert desnivel([0, 1, 0, 1, 0, 5, 4, 6, 8, 2, 0]) == (8.0, 8.0)
    assert desnivel([600, 601, 600.5, 601]) == (0.0, 0.0)


def test_plano_pone_tope_de_desnivel():
    assert interpretar("fondo 18k plano").max_subida_m == 90           # 5 m/km
    assert interpretar("long run 21k flat").max_subida_m == 110        # se redondea hacia arriba
    # En tempo cuentan calentamiento y vuelta a la calma (2 + 8 + 1,5 km).
    assert interpretar("tempo 8k llano").max_subida_m == 60
    # Un número explícito manda.
    assert interpretar("fondo 18k plano +40m").max_subida_m == 40
    assert interpretar("fondo 18k").max_subida_m is None


# ── Pirámides, fartlek y sesiones por tiempo ────────────────────────────────

@pytest.mark.parametrize("texto,escalera,pausa", [
    ("pirámide 200-400-600-400-200", [200, 400, 600, 400, 200], None),
    ("piramide 1-2-3-2-1 km", [1000, 2000, 3000, 2000, 1000], None),
    ("pyramid 400, 800, 1200, 800, 400 r90", [400, 800, 1200, 800, 400], 90),
])
def test_piramide(texto, escalera, pausa):
    s = interpretar(texto)
    assert s.tipo is TipoSesion.SERIES and s.escalera_m == escalera and s.pausa_s == pausa
    assert s.repeticiones == len(escalera) and s.distancia_rep_m == max(escalera)
    assert requisitos(s).largo_tramo_m == max(escalera)       # el tramo, del largo mayor


@pytest.mark.parametrize("texto,reps,rep_s,pausa", [
    ("fartlek 10x1' r1'", 10, 60, 60), ("fartlek 8x3 min / 2 min", 8, 180, 120),
    ("6x30s", 6, 30, None), ('8x45" r30', 8, 45, 30),
])
def test_fartlek_por_tiempo(texto, reps, rep_s, pausa):
    s = interpretar(texto)
    assert (s.tipo, s.repeticiones, s.rep_s, s.pausa_s) == (TipoSesion.SERIES, reps, rep_s, pausa)
    # 1′ a ritmo de 5 km de alguien que trota a 6:00/km: unos 200 m.
    assert interpretar("10x1'").distancia_rep_m == pytest.approx(200, abs=10)
    assert interpretar("10x1'", ritmo_s_km=300).distancia_rep_m > interpretar("10x1'").distancia_rep_m


@pytest.mark.parametrize("texto,tipo,seg", [
    ("45 min suave", TipoSesion.REGENERATIVO, 2700), ("fondo 1h30", TipoSesion.FONDO, 5400),
    ("long run 1h 15 min", TipoSesion.FONDO, 4500), ("tempo 25 min", TipoSesion.TEMPO, 1500),
    ("easy 40'", TipoSesion.REGENERATIVO, 2400), ("45 min", TipoSesion.REGENERATIVO, 2700),
    ("1 hora suave", TipoSesion.REGENERATIVO, 3600),
])
def test_sesion_por_tiempo(texto, tipo, seg):
    s = interpretar(texto)
    assert s.tipo is tipo and s.duracion_s == seg and s.distancia_m % 100 == 0
    # Con un ritmo más rápido, la misma sesión es más larga.
    assert interpretar(texto, ritmo_s_km=300).distancia_m > s.distancia_m


def test_por_tiempo_con_tope_y_resumen():
    s = interpretar("fondo 1h30 plano")
    assert s.distancia_m == 15000 and s.max_subida_m == 80
    assert s.resumen == "fondo 1 h 30 min (≈ 15 km) (máx. +80 m)"
    assert interpretar("fondo 18k").duracion_s is None and interpretar("fondo 18k").distancia_m == 18000
    assert interpretar("pirámide 200-400-200").resumen == "pirámide 200-400-200 m"
