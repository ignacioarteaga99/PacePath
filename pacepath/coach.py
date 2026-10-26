"""PacePath Coach: plan mensual y semanal hacia un objetivo, que se recalcula
con lo que el corredor hace (o no hace), cuidando que la carga quede en rangos
sanos.

Índice de esfuerzo PacePath (IEP)
  Cada carrera vale sus minutos en cada zona ponderados 1–5 (I1–I5, como la
  carga de la revisión), escalados para que 1 hora a umbral (I3) = 100. Con el
  IEP de cada día salen la condición (promedio de 42 días), la fatiga (7 días),
  la frescura (condición − fatiga) y la carga aguda/crónica (fatiga ÷
  condición): 0,8–1,3 es la zona sana; sobre 1,5, el riesgo de lesión sube.

El plan
  Fases básica → específica → competencia hacia la fecha del objetivo, en
  ciclos de 3 semanas de carga y 1 de descarga, con la filosofía noruega: casi
  todo suave, 1–2 sesiones de umbral controlado y poco I5. El volumen de cada
  semana se ancla a lo que se corrió de verdad (nunca más de un 10 % arriba).

Recalcular
  Nada del plan se guarda: cada semana se rehace con las carreras que había
  antes de su lunes, así que las pasadas muestran lo que el Coach sugirió y lo
  que se hizo, y las que vienen se ajustan a lo que pasó. En la semana en
  curso sugiere pasar una calidad que no se hizo a un día suave libre y, si
  la carga se dispara, cambiar la calidad por rodaje suave.

Todo es una recomendación: el Coach es opcional (se activa en Entreno) y el
corredor decide.

Sin datos del servidor: el teléfono manda las carreras (de Strava) y el perfil.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from . import entreno
from .entreno import PESO_CARGA, segundos_por_zona, tipo_sesion, zonas
from .idioma import en, tr

IEP_HORA_UMBRAL = 60 * PESO_CARGA[2]        # 1 h en I3 = 180 «minutos ponderados» = 100 IEP
DIAS_CONDICION, DIAS_FATIGA = 42, 7
SUBIDA_MAX = 1.10                            # una semana no sube más de esto sobre lo corrido
PROGRESION = 1.07                            # semana de carga: +7 % sobre la anterior
DESCARGA, AFINE = 0.75, (0.5, 0.7)           # descarga; semana de la carrera y la anterior

# Por objetivo: rango del volumen pico (km/semana) y fondo más largo (km).
OBJETIVOS = {
    "5k": {"pico": (25, 55), "largo": 14, "dist_m": 5000},
    "10k": {"pico": (30, 65), "largo": 18, "dist_m": 10_000},
    "21k": {"pico": (35, 75), "largo": 22, "dist_m": 21_097.5},
    "42k": {"pico": (45, 90), "largo": 32, "dist_m": 42_195},
    "forma": {"pico": (20, 50), "largo": 16, "dist_m": None},
}
NOMBRE_OBJ = {"5k": ("5K", "5K", "5K"), "10k": ("10K", "10K", "10K"),
              "21k": ("media maratón", "half marathon", "meia maratona"),
              "42k": ("maratón", "marathon", "maratona"),
              "forma": ("mantenerte en forma", "staying fit", "manter a forma")}

# Días de la semana con sesión, relativos al día del fondo (0): «q» calidad,
# «q2» segunda calidad (según fase), «s» suave, «f» fondo.
PLANTILLAS = {
    3: {-5: "q", -3: "s", 0: "f"},
    4: {-6: "s", -5: "q", -3: "q2", 0: "f"},
    5: {-6: "s", -5: "q", -3: "q2", -2: "s", 0: "f"},
    6: {-6: "s", -5: "q", -4: "s", -3: "q2", -2: "s", 0: "f"},
    7: {-6: "s", -5: "q", -4: "s", -3: "q2", -2: "s", -1: "s", 0: "f"},
}
DOBLE_MAX_KM = 10.0          # la 2.ª salida de un día doble: corta y muy suave
PESO_DOBLE = 0.6             # cuánto de un rodaje normal le toca
DIAS = [("lunes", "Monday", "segunda"), ("martes", "Tuesday", "terça"), ("miércoles", "Wednesday", "quarta"),
        ("jueves", "Thursday", "quinta"), ("viernes", "Friday", "sexta"), ("sábado", "Saturday", "sábado"),
        ("domingo", "Sunday", "domingo")]


def _dia(d: date) -> str:
    return tr(*DIAS[d.weekday()])


def _f(x: float, dec: int = 1) -> str:
    s = f"{x:.{dec}f}"
    return s if en() else s.replace(".", ",")


def _ritmo(s_km: float | None) -> str | None:
    if not s_km:
        return None
    s = round(s_km)
    return f"{s // 60}:{s % 60:02d}"


# ── Índice de esfuerzo y estado de carga ────────────────────────────────────

def iep(c: dict, z: dict) -> float:
    """El esfuerzo de una carrera (1 h a umbral = 100)."""
    if z.get("fc") or z.get("ritmo"):
        seg, _ = segundos_por_zona(c, z)
    else:                                        # sin zonas: todo como rodaje suave (I2)
        seg = [0, float(c.get("tiempo_s") or 0), 0, 0, 0]
    return sum(s / 60 * p for s, p in zip(seg, PESO_CARGA)) * 100 / IEP_HORA_UMBRAL


def _fecha(c: dict) -> str:
    return (c.get("fecha_local") or c.get("fecha") or "")[:10]


def carga_diaria(carreras: list[dict], z: dict, hasta: date, dias: int = 120) -> dict[date, float]:
    desde = hasta - timedelta(days=dias - 1)
    out = {desde + timedelta(days=i): 0.0 for i in range(dias)}
    for c in carreras:
        try:
            d = date.fromisoformat(_fecha(c))
        except ValueError:
            continue
        if desde <= d <= hasta:
            out[d] += iep(c, z)
    return out


def estado_carga(carreras: list[dict], z: dict, hoy: date) -> dict:
    """Condición, fatiga, frescura y carga aguda/crónica a hoy, con su semáforo
    y los últimos 42 días para el gráfico."""
    diario = carga_diaria(carreras, z, hoy)
    dias = sorted(diario)
    # Arranque: el promedio de las 4 primeras semanas (si no, todo parte en 0 y la carga aguda se ve enorme).
    ini = sum(diario[d] for d in dias[:28]) / 28
    ctl = atl = ini
    serie = []
    for d in dias:
        ctl += (diario[d] - ctl) / DIAS_CONDICION
        atl += (diario[d] - atl) / DIAS_FATIGA
        serie.append({"fecha": d.isoformat(), "iep": round(diario[d]), "condicion": round(ctl, 1), "fatiga": round(atl, 1)})
    acwr = atl / ctl if ctl >= 5 else None
    tsb = ctl - atl
    if acwr is None:
        nivel, clave = "gris", "pocos_datos"
    elif acwr > 1.5 or tsb < -30:
        nivel, clave = "rojo", "riesgo"
    elif acwr > 1.3 or tsb < -20:
        nivel, clave = "ambar", "al_limite"
    elif acwr < 0.8:
        nivel, clave = "azul", "bajando"
    else:
        nivel, clave = "verde", "sano"
    semana = sum(diario[hoy - timedelta(days=i)] for i in range(7))
    return {"condicion": round(ctl), "fatiga": round(atl), "frescura": round(tsb),
            "acwr": round(acwr, 2) if acwr is not None else None, "iep_7d": round(semana),
            "nivel": nivel, "clave": clave, "texto": _texto_carga(clave, acwr, tsb),
            "serie": serie[-42:]}


def _texto_carga(clave: str, acwr: float | None, tsb: float) -> str:
    a = _f(acwr or 0, 2)
    return {
        "pocos_datos": tr("Todavía hay pocas carreras para medir tu carga: corre un par de semanas y aparece.",
                          "There are still too few runs to measure your load: run for a couple of weeks and it shows up.",
                          "Ainda há poucas corridas para medir sua carga: corra algumas semanas e ela aparece."),
        "riesgo": tr(f"Tu carga de la última semana es ×{a} la de tu condición: alto riesgo de lesión. Toca bajar.",
                     f"Your last week's load is ×{a} your fitness: high injury risk. Time to ease off.",
                     f"Sua carga da última semana é ×{a} a da sua condição: alto risco de lesão. Hora de baixar."),
        "al_limite": tr(f"Carga ×{a} sobre tu condición: al límite de lo sano. Cuida los días suaves.",
                        f"Load ×{a} over your fitness: at the edge of healthy. Keep easy days easy.",
                        f"Carga ×{a} sobre sua condição: no limite do saudável. Cuide dos dias leves."),
        "bajando": tr(f"Carga ×{a}: estás entrenando menos que tu condición; si no es descanso buscado, vas perdiendo forma.",
                      f"Load ×{a}: you're training below your fitness; unless it's planned rest, you're losing shape.",
                      f"Carga ×{a}: você está treinando abaixo da sua condição; se não for descanso planejado, está perdendo forma."),
        "sano": tr(f"Carga ×{a}: en la zona sana (0,8–1,3). Así se mejora sin lesionarse.",
                   f"Load ×{a}: in the healthy zone (0.8–1.3). That's how you improve without getting hurt.",
                   f"Carga ×{a}: na zona saudável (0,8–1,3). Assim você melhora sem se lesionar."),
    }[clave]


# ── Volumen ─────────────────────────────────────────────────────────────────

def _km_semana(carreras: list[dict], lunes: date) -> float:
    fin = lunes + timedelta(days=7)
    return sum((c.get("distancia_m") or 0) / 1000 for c in carreras
               if lunes.isoformat() <= _fecha(c) < fin.isoformat())


def _km4(carreras: list[dict], lunes: date) -> float:
    return sum(_km_semana(carreras, lunes - timedelta(weeks=i)) for i in range(1, 5)) / 4


def _fondo_reciente(carreras: list[dict], lunes: date) -> float:
    desde = (lunes - timedelta(weeks=6)).isoformat()
    return max(((c.get("distancia_m") or 0) / 1000 for c in carreras if desde <= _fecha(c) < lunes.isoformat()),
               default=0.0)


# ── El plan ─────────────────────────────────────────────────────────────────

def _lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


def macro(perfil: dict, hoy: date) -> list[dict]:
    """Las semanas del plan con su fase: hasta la carrera, o un ciclo de 8
    semanas que se renueva («mantenerte en forma»)."""
    inicio = _lunes(date.fromisoformat(perfil["inicio"]))
    objetivo = perfil["objetivo"]
    if objetivo != "forma" and perfil.get("fecha"):
        carrera = date.fromisoformat(perfil["fecha"])
        n = (_lunes(carrera) - inicio).days // 7 + 1
    else:
        carrera = None
        n = max(8, ((_lunes(hoy) - inicio).days // 7) + 6)
    semanas = []
    for i in range(n):
        lunes = inicio + timedelta(weeks=i)
        if carrera:
            k = n - 1 - i                           # semanas que faltan
            fase = ("competencia" if k <= 1 else
                    "especifica" if k <= max(3, round(0.4 * (n - 2))) else "basica")
            descarga = k >= 2 and (k - 2) % 4 == 3
        else:
            k, fase, descarga = None, "basica", i % 4 == 3
        semanas.append({"lunes": lunes, "i": i, "faltan": k, "fase": fase, "descarga": descarga})
    return semanas


def _pico(perfil: dict, base: float) -> float:
    """Volumen máximo del plan: lo típico para el objetivo, pero nunca menos de
    lo que ya se corre (a quien hace 120 km no se le baja a 90)."""
    lo, hi = OBJETIVOS[perfil["objetivo"]]["pico"]
    tipico = min(hi, max(lo, base * (1.2 if perfil["objetivo"] == "forma" else 1.6)))
    return max(tipico, base * (1.0 if perfil["objetivo"] == "forma" else 1.1))


def _meta_km(sem: dict, nivel: float, real_ant: float | None, km4: float, pico: float) -> tuple[float, float]:
    """(km de la semana, nivel): el nivel sube un 7 % por semana sin pasar del
    pico ni de un 10 % sobre lo que de verdad se corrió; la descarga y el
    afinamiento rebajan la semana pero no el nivel (después se sigue desde ahí)."""
    ancla = max(km4, real_ant if real_ant is not None else nivel)
    # Si de verdad se viene corriendo más que el plan (y se aguanta), el plan
    # sube a eso: no se le baja el volumen a quien ya lo tiene.
    nivel = min(pico, ancla * SUBIDA_MAX, max(nivel * PROGRESION, ancla))
    meta = nivel
    if sem["descarga"]:
        meta = nivel * DESCARGA
    if sem["faltan"] is not None and sem["faltan"] <= 1:
        meta = nivel * AFINE[sem["faltan"]]
    return max(meta, 8.0), nivel


def _zonas_ritmo(z: dict) -> list[tuple[float | None, float | None]]:
    return [(r["desde"], r["hasta"]) for r in z["ritmo"]] if z.get("ritmo") else []


def _ritmo_zona(z: dict, i: int) -> str | None:
    """«4:15–4:30» para la zona I(i+1) (en ritmo: desde = más lento)."""
    rz = _zonas_ritmo(z)
    if not rz:
        return None
    lento, rapido = rz[i]
    if lento and rapido:
        return f"{_ritmo(rapido)}–{_ritmo(lento)}"
    return f"< {_ritmo(lento)}" if lento else f"> {_ritmo(rapido)}"


def _ritmo_carrera(z: dict, dist: str) -> str | None:
    p = (z.get("predicciones") or {}).get(dist)
    return _ritmo(p / (entreno.DISTANCIAS[dist] / 1000)) if p else None


def _sesion_calidad(perfil: dict, sem: dict, z: dict, cual: str, n_fase: int) -> dict:
    """La sesión de calidad (texto que PacePath entiende, zona, km y ritmo)."""
    obj, fase = perfil["objetivo"], sem["fase"]
    sube = min(3, n_fase // 2)                        # cada 2 semanas de la fase, un poco más
    if fase == "competencia":
        if sem["faltan"] == 0:
            return {"texto": "4x400", "tipo": "intensa", "zona": 4, "trabajo_km": 1.6,
                    "nota": tr("Afilar sin cansar: cuatro rectas vivas para despertar las piernas.",
                               "Sharpen without tiring: four lively reps to wake your legs up.",
                               "Afiar sem cansar: quatro tiros vivos para acordar as pernas.")}
        return {"texto": "tempo 4k", "tipo": "umbral", "zona": 2, "trabajo_km": 4,
                "nota": tr("Afinamiento: se mantiene la intensidad y baja el volumen.",
                           "Taper: intensity stays, volume drops.",
                           "Polimento: a intensidade se mantém e o volume cai.")}
    if fase == "basica" or cual == "q2":
        reps = 5 + sube
        if cual == "q2" and fase == "basica":
            return {"texto": f"{3 + min(2, sube)}x2k", "tipo": "umbral", "zona": 2, "trabajo_km": 2 * (3 + min(2, sube)),
                    "nota": tr("Segundo umbral de la semana (el «doble umbral» noruego): controlado, sin terminar fundido.",
                               "Second threshold of the week (the Norwegian «double threshold»): controlled, don't finish wrecked.",
                               "Segundo limiar da semana (o «duplo limiar» norueguês): controlado, sem terminar destruído.")}
        return {"texto": f"{reps}x1k", "tipo": "umbral", "zona": 2, "trabajo_km": reps,
                "nota": tr("Umbral controlado con 1′ de pausa: un ritmo que podrías repetir. Es lo que más rinde.",
                           "Controlled threshold with 1′ rest: a pace you could repeat. It's the best return on effort.",
                           "Limiar controlado com 1′ de pausa: um ritmo que você conseguiria repetir. É o que mais rende.")}
    # Específica: lo que pide la distancia.
    if obj == "5k":
        n = 8 + 2 * min(2, sube)
        return {"texto": f"{n}x400", "tipo": "intensa", "zona": 4, "trabajo_km": n * 0.4,
                "nota": tr("Ritmo de 5K o algo más rápido, pausa de 1′: velocidad para el día de la carrera.",
                           "5K pace or a bit faster, 1′ rest: speed for race day.",
                           "Ritmo de 5K ou um pouco mais rápido, pausa de 1′: velocidade para o dia da prova.")}
    if obj == "10k":
        n = 4 + min(2, sube)
        return {"texto": f"{n}x1600", "tipo": "intensa", "zona": 3, "trabajo_km": n * 1.6,
                "nota": tr("A ritmo de 10K con 2′ de pausa: acostumbra el cuerpo al ritmo objetivo.",
                           "At 10K pace with 2′ rest: get your body used to goal pace.",
                           "No ritmo de 10K com 2′ de pausa: acostuma o corpo ao ritmo da prova.")}
    if obj == "21k":
        n = 2 + min(2, sube)
        return {"texto": f"{n}x3k", "tipo": "umbral", "zona": 2, "trabajo_km": 3 * n,
                "nota": tr("A ritmo de media maratón, 2′ de pausa: el ritmo que vas a sostener.",
                           "At half-marathon pace, 2′ rest: the pace you'll hold on race day.",
                           "No ritmo de meia maratona, 2′ de pausa: o ritmo que você vai sustentar.")}
    km = 10 + 2 * min(3, sube)
    return {"texto": f"tempo {km}k", "tipo": "umbral", "zona": 2, "trabajo_km": km,
            "nota": tr("A ritmo de maratón: largo y controlado, es la sesión clave.",
                       "At marathon pace: long and controlled, it's the key session.",
                       "No ritmo de maratona: longo e controlado, é a sessão-chave.")}


def _ritmo_sesion(perfil: dict, z: dict, s: dict) -> str | None:
    obj = perfil["objetivo"]
    if s["tipo"] in ("suave", "fondo"):
        # Suave de verdad: desde el borde lento de I2 hacia abajo (I2 entera queda rápida para un rodaje).
        rz = _zonas_ritmo(z)
        if rz and rz[1][0]:
            return f"{_ritmo(rz[1][0])}–{_ritmo(rz[1][0] * 1.12)}"
        return None
    if s["tipo"] == "intensa" and s["zona"] == 4:
        return _ritmo_carrera(z, "5k") or _ritmo_zona(z, 4)
    if s["tipo"] == "intensa" and obj == "10k":
        return _ritmo_carrera(z, "10k") or _ritmo_zona(z, 3)
    if s["tipo"] == "umbral" and s.get("objetivo_ritmo") in ("21k", "42k"):
        return _ritmo_carrera(z, s["objetivo_ritmo"]) or _ritmo_zona(z, 2)
    return _ritmo_zona(z, s["zona"])


def _min_por_km(z: dict, zona: int) -> float:
    rz = _zonas_ritmo(z)
    if not rz:
        return [7.0, 6.2, 5.3, 5.0, 4.6][zona]
    lento, rapido = rz[zona]
    s = (lento + rapido) / 2 if lento and rapido else (lento or rapido)
    return s / 60


def plan_semana(perfil: dict, sem: dict, z: dict, km: float, fondo_prev: float, n_fase: int) -> list[dict]:
    """Las sesiones de una semana: una por día con entrenamiento, y en los días
    dobles una 2.ª salida suave («turno» 2)."""
    obj = perfil["objetivo"]
    dias = PLANTILLAS[min(7, max(3, int(perfil.get("dias") or 4)))]
    largo_dia = int(perfil.get("dia_largo", 6))
    q2_calidad = (sem["fase"] == "especifica" or (sem["fase"] == "basica" and len(dias) >= 5)) and not sem["descarga"]
    carrera = date.fromisoformat(perfil["fecha"]) if sem["faltan"] == 0 and perfil.get("fecha") else None
    # El fondo: ~28 % de la semana (un tercio con 3 días), sin pasar del tope del objetivo
    # ni crecer más de 2 km sobre el más largo reciente.
    frac = 0.33 if len(dias) == 3 else 0.28
    largo = min(OBJETIVOS[obj]["largo"], km * frac, max(fondo_prev + 2, 8))
    if sem["descarga"]:
        largo *= 0.8
    out = []
    for rel, tipo in sorted(dias.items()):
        d = sem["lunes"] + timedelta(days=(largo_dia + rel) % 7)
        if carrera and d > carrera:
            continue
        if tipo in ("q", "q2") and (tipo == "q" or q2_calidad):
            s = _sesion_calidad(perfil, sem, z, tipo, n_fase)
            if obj in ("21k", "42k") and sem["fase"] == "especifica" and tipo == "q":
                s["objetivo_ritmo"] = obj
            s["km"] = round(s["trabajo_km"] + 4, 1)          # + calentamiento y vuelta a la calma
            s["titulo"] = (tr("Calidad · umbral", "Quality · threshold", "Qualidade · limiar") if s["tipo"] == "umbral"
                           else tr("Calidad · velocidad", "Quality · speed", "Qualidade · velocidade"))
        elif tipo == "f":
            s = {"tipo": "fondo", "zona": 1, "km": round(largo), "texto": f"fondo {round(largo)}k",
                 "titulo": tr("Fondo", "Long run", "Longão"),
                 "nota": tr("Suave y conversable: construye la base aeróbica.",
                            "Easy and conversational: it builds your aerobic base.",
                            "Leve e dando para conversar: constrói a base aeróbica.")}
            if obj in ("21k", "42k") and sem["fase"] == "especifica" and not sem["descarga"]:
                ult = 5 if obj == "21k" else 8
                s["nota"] = tr(f"Suave, y los últimos {ult} km a ritmo de carrera.",
                               f"Easy, with the last {ult} km at race pace.",
                               f"Leve, com os últimos {ult} km no ritmo da prova.")
        else:
            s = {"tipo": "suave", "zona": 1, "texto": None, "titulo": tr("Rodaje suave", "Easy run", "Rodagem leve"),
                 "nota": tr("Suave de verdad (I1–I2): recupera y suma volumen sin cansar.",
                            "Truly easy (I1–I2): recover and add volume without fatigue.",
                            "Leve de verdade (I1–I2): recupera e soma volume sem cansar.")}
        s["fecha"] = d.isoformat()
        out.append(s)
    # Dobles: una 2.ª salida suave y corta, primero en los días de calidad (series
    # en la mañana, trote en la tarde) y después en los suaves; nunca el día del
    # fondo ni en la semana de la carrera. En descarga, la mitad.
    n_dobles = int(perfil.get("dobles") or 0)
    if sem["descarga"]:
        n_dobles //= 2
    if sem["faltan"] is not None and sem["faltan"] == 0:
        n_dobles = 0
    candidatos = [x for x in out if x["tipo"] in ("umbral", "intensa")] + [x for x in out if x["tipo"] == "suave"]
    for x in candidatos[:n_dobles]:
        out.append({"tipo": "suave", "zona": 1, "texto": None, "fecha": x["fecha"], "turno": 2,
                    "titulo": tr("Rodaje suave · 2.ª salida", "Easy run · 2nd run", "Rodagem leve · 2.ª saída"),
                    "nota": tr("Corto y muy suave, con al menos 6 h entre una salida y otra: suma volumen casi sin cansar.",
                               "Short and very easy, at least 6 h apart from the other run: adds volume with little fatigue.",
                               "Curta e bem leve, com pelo menos 6 h entre uma saída e outra: soma volume quase sem cansar.")})
    # Los suaves reparten lo que falta para la meta de la semana, sin pasar de
    # ~70 % del fondo (un rodaje no es otro fondo) y los dobles con menos: si no
    # cabe, la semana queda más corta y el Coach lo dice.
    suaves = [x for x in out if x["tipo"] == "suave"]
    resto = km - sum(x["km"] for x in out if x["tipo"] != "suave")
    tope = max(6.0, min(18.0, 0.7 * largo))
    unidad = resto / max(sum(PESO_DOBLE if x.get("turno") == 2 else 1 for x in suaves), 1)
    for x in suaves:
        x["km"] = (round(min(DOBLE_MAX_KM, max(4.0, PESO_DOBLE * unidad))) if x.get("turno") == 2
                   else round(min(tope, max(4.0, unidad))))
        x["texto"] = f"regenerativo {x['km']}k"
    if carrera:
        dist = OBJETIVOS[obj]["dist_m"] / 1000
        out = [s for s in out if s["fecha"] != carrera.isoformat()]
        out.append({"tipo": "carrera", "zona": 3, "km": round(dist, 1), "texto": None, "fecha": carrera.isoformat(),
                    "titulo": tr(f"¡Carrera! {tr(*NOMBRE_OBJ[obj])}", f"Race day! {tr(*NOMBRE_OBJ[obj])}",
                                 f"Dia de prova! {tr(*NOMBRE_OBJ[obj])}"),
                    "nota": tr("Parte controlado y acelera en la segunda mitad. ¡A disfrutar!",
                               "Start controlled and pick it up in the second half. Enjoy it!",
                               "Comece controlado e acelere na segunda metade. Aproveite!")})
    for s in out:
        s["ritmo"] = _ritmo_sesion(perfil, z, s) if s["tipo"] != "carrera" else _ritmo_carrera(z, obj if obj != "forma" else "10k")
        trabajo = s.get("trabajo_km", 0)
        minutos = (s["km"] - trabajo) * _min_por_km(z, 1) + trabajo * _min_por_km(z, min(s["zona"], 4))
        s["minutos"] = round(minutos)
        peso = PESO_CARGA[1] * (minutos - trabajo * _min_por_km(z, min(s["zona"], 4))) + \
            PESO_CARGA[min(s["zona"], 4)] * trabajo * _min_por_km(z, min(s["zona"], 4))
        s["iep"] = round(peso * 100 / IEP_HORA_UMBRAL)
        s.pop("trabajo_km", None)
        s.pop("objetivo_ritmo", None)
    return sorted(out, key=lambda s: (s["fecha"], s.get("turno", 1)))


# ── Lo hecho contra lo pedido ───────────────────────────────────────────────

CALIDAD = ("umbral", "intensa", "carrera")


def _tipo_real(c: dict, z: dict) -> str:
    if z.get("fc") or z.get("ritmo"):
        seg, fuente = segundos_por_zona(c, z)
    else:
        seg, fuente = [0, float(c.get("tiempo_s") or 0), 0, 0, 0], "sin_datos"
    return tipo_sesion(c, seg, fuente)


def _comparar(sesiones: list[dict], carreras: list[dict], z: dict, lunes: date, hoy: date) -> list[dict]:
    """Marca cada sesión: hecha · parcial · saltada · hoy · pendiente; y agrega
    las carreras que no corresponden a ninguna («extra»). Cada sesión busca su
    carrera el mismo día o uno antes/después (el fondo del sábado en vez del
    domingo cuenta): primero la carrera, después la calidad, el fondo y los suaves."""
    fin = lunes + timedelta(days=7)
    libres = [{"c": c, "f": date.fromisoformat(_fecha(c)), "tipo": _tipo_real(c, z),
               "km": (c.get("distancia_m") or 0) / 1000}
              for c in carreras if lunes.isoformat() <= _fecha(c) < fin.isoformat()]
    orden = {"carrera": 0, "umbral": 1, "intensa": 1, "fondo": 2, "suave": 3}
    for s in sorted(sesiones, key=lambda s: orden.get(s["tipo"], 4)):
        d = date.fromisoformat(s["fecha"])
        pidio_calidad = s["tipo"] in CALIDAD

        def puntaje(r):
            calidad = r["tipo"] in ("umbral", "intensa", "competencia")
            return (abs((r["f"] - d).days),                       # mejor el mismo día
                    0 if calidad == pidio_calidad else 1,         # y del tipo pedido
                    -r["km"] if s["tipo"] == "fondo" else abs(r["km"] - s["km"]))
        # Una sesión futura no toma carreras ya hechas; y en otro día, sólo una del
        # mismo carácter (un rodaje suave del lunes no «cumple» la calidad del martes).
        candidatas = [] if d > hoy else [
            r for r in libres if r["f"] == d or (abs((r["f"] - d).days) == 1 and
                                                 (r["tipo"] in ("umbral", "intensa", "competencia")) == pidio_calidad)]
        if candidatas:
            r = min(candidatas, key=puntaje)
            libres.remove(r)
            hizo_calidad = r["tipo"] in ("umbral", "intensa", "competencia")
            ok = r["km"] >= 0.7 * s["km"] and (hizo_calidad or not pidio_calidad)
            s["estado"] = "hecha" if ok else "hoy" if d == hoy else "parcial"     # hoy puede venir otra salida
            s["real"] = {"km": round(r["km"], 1), "tipo": r["tipo"], "iep": round(iep(r["c"], z)), "fecha": r["f"].isoformat()}
        elif s["fecha"] < hoy.isoformat():
            s["estado"] = "saltada"
        else:
            s["estado"] = "hoy" if s["fecha"] == hoy.isoformat() else "pendiente"
    extras = [{"fecha": r["f"].isoformat(), "tipo": "extra", "estado": "extra", "km": round(r["km"], 1), "zona": 1,
               "titulo": tr("Carrera extra", "Extra run", "Corrida extra"), "texto": None,
               "real": {"km": round(r["km"], 1), "tipo": r["tipo"], "iep": round(iep(r["c"], z))}} for r in libres]
    return sorted(sesiones + extras, key=lambda s: (s["fecha"], s.get("turno", 1)))


def _ajustar_semana(sesiones: list[dict], hoy: date, carga: dict) -> list[str]:
    """La semana en curso: mover la calidad saltada y bajar si la carga se
    disparó. Devuelve los mensajes del Coach."""
    mensajes = []
    futuras = [s for s in sesiones if s["estado"] in ("hoy", "pendiente")]
    # 1. Calidad saltada o hecha suave → al próximo día suave que no quede pegado a otra calidad ni al fondo.
    for s in sesiones:
        if s["tipo"] in ("umbral", "intensa") and s["estado"] in ("saltada", "parcial") and not s.get("movida"):
            duros = {x["fecha"] for x in sesiones if x["tipo"] in ("umbral", "intensa", "fondo", "carrera")
                     and x["estado"] in ("hoy", "pendiente")}
            for f in futuras:
                d = date.fromisoformat(f["fecha"])
                vecinos = {(d + timedelta(days=k)).isoformat() for k in (-1, 1)}
                if f["tipo"] == "suave" and f.get("turno") != 2 and not (vecinos & duros):
                    nueva = {k: v for k, v in s.items() if k not in ("estado", "real")}
                    nueva.update(fecha=f["fecha"], estado=f["estado"], movida=s["fecha"])
                    f.clear(); f.update(nueva)
                    s["movida_a"] = nueva["fecha"]
                    mensajes.append(tr(f"Te sugiero pasar la sesión de calidad del {_dia(date.fromisoformat(s['fecha']))} al "
                                       f"{_dia(d)}: es la que más rinde en la semana.",
                                       f"I suggest moving the quality session from {_dia(date.fromisoformat(s['fecha']))} to "
                                       f"{_dia(d)}: it's the most valuable one of the week.",
                                       f"Sugiro passar a sessão de qualidade de {_dia(date.fromisoformat(s['fecha']))} para "
                                       f"{_dia(d)}: é a que mais rende na semana."))
                    break
            else:
                if s["estado"] == "saltada":
                    mensajes.append(tr("No hiciste la calidad y no queda un día que no choque con otra: te recomiendo no recuperarla y seguir.",
                                       "You didn't do the quality session and no day is left that doesn't clash: my advice is not to make it up and carry on.",
                                       "Você não fez a qualidade e não sobra um dia sem choque: recomendo não recuperar e seguir."))
    # 2. Carga en rojo: lo que queda de calidad pasa a suave.
    if carga["nivel"] == "rojo":
        for f in sesiones:
            if f["estado"] in ("hoy", "pendiente") and f["tipo"] in ("umbral", "intensa"):
                f.update(tipo="suave", titulo=tr("Mejor un rodaje suave", "Better an easy run", "Melhor uma rodagem leve"),
                         texto=f"regenerativo {round(f['km'] * 0.8)}k", km=round(f["km"] * 0.8), zona=1,
                         nota=tr("Tocaba calidad, pero tu carga está muy alta: te recomiendo suave para absorber lo hecho.",
                                 "This was a quality day, but your load is very high: I recommend easy to absorb the work.",
                                 "Era dia de qualidade, mas sua carga está muito alta: recomendo leve para absorver o treino."))
        mensajes.append(tr("Tu carga subió demasiado: te recomiendo cambiar la calidad que queda esta semana por rodajes suaves.",
                           "Your load went up too much: I recommend swapping this week's remaining quality for easy runs.",
                           "Sua carga subiu demais: recomendo trocar a qualidade que falta nesta semana por rodagens leves."))
    return mensajes


# ── Todo junto ──────────────────────────────────────────────────────────────

def plan(perfil: dict, carreras: list[dict], ajustes: dict, hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    obj = perfil.get("objetivo")
    if obj not in OBJETIVOS:
        return {"perfil": None}
    perfil = {"dias": 4, "dia_largo": 6, "inicio": hoy.isoformat()} | {k: v for k, v in perfil.items() if v is not None}
    if obj != "forma" and (not perfil.get("fecha") or date.fromisoformat(perfil["fecha"]) < hoy):
        return {"perfil": perfil, "error": tr("Elige la fecha de tu carrera (desde hoy en adelante).",
                                              "Pick your race date (from today on).",
                                              "Escolha a data da sua prova (de hoje em diante).")}
    hace_un_año = (hoy - timedelta(days=365)).isoformat()
    picos = [(c.get("hist") or {}).get("fc_max") or c.get("fc_max") for c in carreras if _fecha(c) >= hace_un_año]
    z = zonas(ajustes, max((p for p in picos if p and 120 < p < 230), default=None))
    carga = estado_carga(carreras, z, hoy)
    semanas = macro(perfil, hoy)
    lunes_hoy = _lunes(hoy)
    base = max(_km4(carreras, semanas[0]["lunes"]), 3 * 4.0)
    pico = _pico(perfil, max(base, _km4(carreras, lunes_hoy)))
    # Semana a semana: las pasadas con las carreras de antes de su lunes; las
    # que vienen, suponiendo que se cumple lo pedido.
    nivel, n_fase, fase_ant, out = base, 0, None, []
    for sem in semanas:
        antes = [c for c in carreras if _fecha(c) < sem["lunes"].isoformat()]
        pasada_o_actual = sem["lunes"] <= lunes_hoy
        real_ant = _km_semana(carreras, sem["lunes"] - timedelta(weeks=1)) if pasada_o_actual and sem["i"] else None
        km4 = _km4(antes, sem["lunes"]) if pasada_o_actual else nivel
        km, nivel = _meta_km(sem, nivel, real_ant, km4, pico)
        n_fase = n_fase + 1 if sem["fase"] == fase_ant else 0
        fase_ant = sem["fase"]
        fondo_prev = (_fondo_reciente(antes, sem["lunes"]) if pasada_o_actual
                      else max((w["fondo_km"] for w in out[-3:]), default=0))
        sesiones = plan_semana(perfil, sem, z, km, fondo_prev, n_fase)
        fila = {"lunes": sem["lunes"].isoformat(), "fase": sem["fase"], "descarga": sem["descarga"], "meta_km": round(km),
                "faltan": sem["faltan"], "km": round(sum(s["km"] for s in sesiones)),
                "fondo_km": max((s["km"] for s in sesiones if s["tipo"] == "fondo"), default=0),
                "calidad": sum(s["tipo"] in ("umbral", "intensa") for s in sesiones)}
        if pasada_o_actual:
            sesiones = _comparar(sesiones, carreras, z, sem["lunes"], hoy)
            hechas = [s for s in sesiones if s["estado"] in ("hecha", "parcial", "extra")]
            fila["km_real"] = round(sum(s.get("real", {}).get("km", 0) for s in hechas), 1)
            pedidas = [s for s in sesiones if s["estado"] != "extra" and s["fecha"] < hoy.isoformat()]
            fila["cumplidas"] = sum(s["estado"] == "hecha" for s in pedidas)
            fila["pedidas"] = len(pedidas)
        fila["sesiones"] = sesiones
        out.append(fila)
    actual = next((w for w in out if w["lunes"] == lunes_hoy.isoformat()), None)
    mensajes = _ajustar_semana(actual["sesiones"], hoy, carga) if actual else []
    mensajes = _resumen(out, lunes_hoy, carga, perfil) + mensajes
    de_hoy = [s for s in (actual or {}).get("sesiones", []) if s["fecha"] == hoy.isoformat() and s["tipo"] != "extra"]
    hoy_s = de_hoy[0] if de_hoy else None
    i_act = next((i for i, w in enumerate(out) if w["lunes"] == lunes_hoy.isoformat()), len(out) - 1)
    return {"perfil": perfil, "objetivo": tr(*NOMBRE_OBJ[obj]), "carga": carga, "hoy": hoy_s,
            "hoy_2": de_hoy[1] if len(de_hoy) > 1 else None,
            "semana": actual, "mensajes": mensajes,
            "semanas": [{k: v for k, v in w.items() if k != "sesiones"} for w in out[max(0, i_act - 3):i_act + 6]],
            "faltan_semanas": actual["faltan"] if obj != "forma" and actual else None,
            "zonas_ok": bool(z.get("ritmo") or z.get("fc"))}


def _resumen(out: list[dict], lunes_hoy: date, carga: dict, perfil: dict) -> list[str]:
    """El saludo del Coach: cómo fue la semana pasada y qué viene."""
    msgs = []
    pasada = next((w for w in out if w["lunes"] == (lunes_hoy - timedelta(weeks=1)).isoformat()), None)
    if pasada and pasada.get("pedidas"):
        c, p, kr, kp = pasada["cumplidas"], pasada["pedidas"], pasada["km_real"], pasada["km"]
        if c == p:
            msgs.append(tr(f"Semana pasada perfecta: {c} de {p} sesiones y {_f(kr)} km. Seguimos subiendo con cuidado.",
                           f"Perfect last week: {c} of {p} sessions and {_f(kr)} km. We keep building carefully.",
                           f"Semana passada perfeita: {c} de {p} sessões e {_f(kr)} km. Seguimos subindo com cuidado."))
        elif kr > 1.25 * max(kp, pasada.get("meta_km", 0)):
            msgs.append(tr(f"La semana pasada corriste {_f(kr)} km, bastante más que los {kp} del plan. Si te sientes "
                           "bien, perfecto, pero vigila tu carga: subir rápido es la causa n.º 1 de lesiones.",
                           f"Last week you ran {_f(kr)} km, well above the plan's {kp}. If you feel good, great, but "
                           "watch your load: ramping up fast is the #1 cause of injuries.",
                           f"Semana passada você correu {_f(kr)} km, bem mais que os {kp} do plano. Se está se sentindo "
                           "bem, ótimo, mas cuide da carga: subir rápido é a causa n.º 1 de lesões."))
        elif kr < 0.6 * kp:
            msgs.append(tr(f"La semana pasada hiciste {c} de {p} sesiones ({_f(kr)} de {kp} km). No pasa nada: "
                           "te propongo esta semana según lo que de verdad corriste, sin intentar recuperar.",
                           f"Last week you did {c} of {p} sessions ({_f(kr)} of {kp} km). No problem: I suggest this "
                           "week based on what you actually ran, without trying to make it up.",
                           f"Semana passada você fez {c} de {p} sessões ({_f(kr)} de {kp} km). Sem problema: "
                           "proponho esta semana com base no que você realmente correu, sem tentar recuperar."))
        else:
            msgs.append(tr(f"Semana pasada: {c} de {p} sesiones y {_f(kr)} de {kp} km. Bien encaminado.",
                           f"Last week: {c} of {p} sessions and {_f(kr)} of {kp} km. On track.",
                           f"Semana passada: {c} de {p} sessões e {_f(kr)} de {kp} km. No caminho certo."))
    actual = next((w for w in out if w["lunes"] == lunes_hoy.isoformat()), None)
    if actual:
        fase = {"basica": tr("básica", "base", "básica"), "especifica": tr("específica", "specific", "específica"),
                "competencia": tr("de competencia", "race", "de competição")}[actual["fase"]]
        if actual["faltan"] == 0:
            msgs.append(tr("¡Semana de carrera! Poco volumen, piernas frescas y confianza: el trabajo ya está hecho.",
                           "Race week! Low volume, fresh legs and confidence: the work is done.",
                           "Semana de prova! Pouco volume, pernas descansadas e confiança: o trabalho já está feito."))
        elif actual["descarga"]:
            msgs.append(tr(f"Esta semana es de descarga ({actual['km']} km): el cuerpo asimila y vuelve más fuerte.",
                           f"This is a recovery week ({actual['km']} km): your body absorbs the work and comes back stronger.",
                           f"Esta é uma semana de descarga ({actual['km']} km): o corpo assimila e volta mais forte."))
        else:
            if actual["km"] < 0.8 * actual["meta_km"]:
                msgs.append(tr(f"Tu volumen sano sería ~{actual['meta_km']} km, pero con {perfil['dias']} días y "
                               f"{perfil.get('dobles') or 0} dobles no cabe sin rodajes demasiado largos: si puedes, "
                               "suma un día o una doble jornada.",
                               f"Your healthy volume would be ~{actual['meta_km']} km, but with {perfil['dias']} days and "
                               f"{perfil.get('dobles') or 0} doubles it doesn't fit without overly long easy runs: add a "
                               "day or a double if you can.",
                               f"Seu volume saudável seria ~{actual['meta_km']} km, mas com {perfil['dias']} dias e "
                               f"{perfil.get('dobles') or 0} dobras não cabe sem rodagens longas demais: se puder, some "
                               "um dia ou uma dobra."))
            msgs.append(tr(f"Fase {fase}: {actual['km']} km esta semana, con {actual['calidad']} de calidad y fondo de {actual['fondo_km']} km.",
                           f"{fase.capitalize()} phase: {actual['km']} km this week, with {actual['calidad']} quality "
                           f"session(s) and a {actual['fondo_km']} km long run.",
                           f"Fase {fase}: {actual['km']} km nesta semana, com {actual['calidad']} de qualidade e longão de {actual['fondo_km']} km."))
    return msgs
