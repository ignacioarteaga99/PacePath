"""Zonas de entrenamiento y revisión de los últimos 3 meses (pestaña «Entreno»).

Filosofía: la del método noruego (Olympiatoppen, los Ingebrigtsen, Bakken).
Mucho volumen suave de verdad (I1–I2), dos sesiones de umbral controlado por
semana (I3–I4, justo bajo el umbral), poco I5 y nada de «zona gris» en los días
suaves. Es una distribución piramidal más que polarizada.

Zonas: la escala de intensidad de Olympiatoppen (I1–I5), en pulsaciones y en
ritmo.

· Pulso. Con la FC umbral (la de ~1 h a tope) se ancla todo en ella; si no,
  con FC máxima y de reposo (Karvonen), o sólo FC máxima (% FCmáx). Sin nada,
  la FC máxima se estima con lo más alto que registraron tus carreras.
· Ritmo. El ritmo umbral escrito manda; si no, se calcula con tus mejores
  5K, 10K, 21K o 42K (el de mejor VDOT de Daniels) y su ritmo T (umbral).

Revisión: tiempo en cada zona por carrera (del pulso o, sin pulsómetro, del
ritmo, segundo a segundo; si no hay detalle, la carrera entera en la zona de
su promedio), semanas, índice de polarización (Treff et al., 2019), sesiones
de umbral, rodajes suaves demasiado rápidos, carga aguda/crónica y fondo largo;
todo comparado con lo que corresponde a la fase (básica, específica o
competencia). Nada se guarda: el teléfono manda sus carreras y recibe esto.
"""
from __future__ import annotations

import bisect
import math
import re
from datetime import date, datetime, timedelta

from .idioma import en, tr

ZONAS = ("I1", "I2", "I3", "I4", "I5")
# Olympiatoppen en % de la FC máxima: I1 55–72 · I2 72–82 · I3 82–87 · I4 87–92 · I5 92–97+.
FRAC_FCMAX = (0.72, 0.82, 0.87, 0.92)
# Las mismas fronteras en % de la reserva de pulso (Karvonen) y de la FC umbral
# (que en la escala cae en el 87 % de la FC máxima).
FRAC_RESERVA = (0.60, 0.75, 0.82, 0.89)
FRAC_UMBRAL = tuple(round(f / 0.87, 3) for f in FRAC_FCMAX)      # 0.828 · 0.943 · 1 · 1.057
# Ritmo: fracción de la velocidad umbral (I3 llega hasta el umbral; el «doble
# umbral» noruego se corre a ~95–98 % de esa velocidad).
FRAC_VEL_UMBRAL = (0.80, 0.90, 1.00, 1.05)
SEMANAS = 12
UMBRAL_MIN_S = 12 * 60          # tiempo en I3–I4 para contar una sesión de umbral
I5_MIN_S = 5 * 60               # tiempo en I5 para contar una de alta intensidad
# Un nombre así describe una sesión estructurada: «2x3000», «3'20"/km», «pausa 2'», «umbral»…
PALABRAS_CALIDAD = re.compile(
    r"\d+\s*[x×]\s*\d|series|interval|umbral|threshold|tempo|fartlek|cuesta|hill|repet|reps|pista|track|"
    r"progresiv|progression|lactato|doble|double|pausa|recup|\brec\b|\brest\b|\d\s*['’′]\s*\d{2}|/km|\bsub\s*\d", re.I)
ESTRUCTURADA_MIN_S = 5 * 60     # en una sesión estructurada basta esto en I3+ (el pulso va atrasado en series)


# ── Daniels: VDOT y ritmos ──────────────────────────────────────────────────

def _vo2(v_m_min: float) -> float:
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def _fraccion(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def vdot(distancia_m: float, tiempo_s: float) -> float:
    t = tiempo_s / 60
    return _vo2(distancia_m / t) / _fraccion(t)


def _vel_para(vo2: float) -> float:
    """m/min con ese consumo (la inversa de _vo2)."""
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def tiempo_equivalente(v: float, distancia_m: float) -> float:
    """Segundos para `distancia_m` con este VDOT (bisección: el tiempo con el
    que vdot(distancia, t) = v)."""
    lo, hi = 60.0, 60 * 60 * 8
    for _ in range(60):
        mid = (lo + hi) / 2
        if vdot(distancia_m, mid) > v:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def ritmo_umbral_de_vdot(v: float) -> float:
    """s/km del umbral: el ritmo T de Daniels (88 % del VDOT). Algo más lento
    que lo que se aguanta una hora a tope, que es justo lo que busca el umbral
    «controlado» noruego."""
    return 60_000 / _vel_para(v * 0.88)


# ── Zonas ───────────────────────────────────────────────────────────────────

def _rangos(fronteras: list[float]) -> list[dict]:
    lim = [None, *fronteras, None]
    return [{"zona": z, "desde": lim[i], "hasta": lim[i + 1]} for i, z in enumerate(ZONAS)]


DISTANCIAS = {"5k": 5000, "10k": 10_000, "21k": 21_097.5, "42k": 42_195}


def zonas(ajustes: dict, fc_max_detectada: float | None = None) -> dict:
    """Zonas de pulso y de ritmo con lo que haya: `ajustes` trae (todos
    opcionales) fc_max, fc_reposo, fc_umbral, t5k_s, t10k_s, t21k_s, t42k_s y
    ritmo_umbral_s. Devuelve además qué se usó: `fuente_fc`, `fuente_ritmo`,
    el VDOT de cada tiempo (`vdots`) y cuál mandó (`vdot_de`)."""
    out: dict = {"fc": None, "ritmo": None, "fuente_fc": None, "fuente_ritmo": None,
                 "fc_max_detectada": round(fc_max_detectada) if fc_max_detectada else None}
    fc_max = ajustes.get("fc_max") or None
    reposo, umbral = ajustes.get("fc_reposo") or None, ajustes.get("fc_umbral") or None
    if umbral:
        b, out["fuente_fc"] = [umbral * f for f in FRAC_UMBRAL], "umbral"
    elif fc_max and reposo:
        b, out["fuente_fc"] = [reposo + (fc_max - reposo) * f for f in FRAC_RESERVA], "reserva"
    elif fc_max or fc_max_detectada:
        m = fc_max or fc_max_detectada
        b, out["fuente_fc"] = [m * f for f in FRAC_FCMAX], "fc_max" if fc_max else "fc_max_detectada"
    else:
        b = None
    if b:
        out["fc"] = _rangos([round(x) for x in b])
        out["fc_umbral"] = round(b[2])          # frontera I3/I4
        out["fc_aerobico"] = round(b[1])        # frontera I2/I3: el «umbral aeróbico»
    # Ritmo: el VDOT de cada mejor tiempo; manda el más alto (la mejor forma).
    vd, umbral_s = None, ajustes.get("ritmo_umbral_s") or None
    vdots = {k: vdot(d, ajustes[f"t{k}_s"]) for k, d in DISTANCIAS.items()
             if ajustes.get(f"t{k}_s") and 150 <= ajustes[f"t{k}_s"] / (d / 1000) <= 720}   # entre 2:30 y 12:00/km
    if vdots:
        out["vdot_de"] = max(vdots, key=vdots.get)
        vd = vdots[out["vdot_de"]]
        out["vdots"] = {k: round(v, 1) for k, v in vdots.items()}
        out["vdot"] = round(vd, 1)
        out["predicciones"] = {k: round(tiempo_equivalente(vd, d)) for k, d in DISTANCIAS.items()}
    if umbral_s:
        out["fuente_ritmo"] = "umbral"
    elif vd:
        umbral_s, out["fuente_ritmo"] = ritmo_umbral_de_vdot(vd), "vdot"
    if umbral_s:
        # Fronteras de velocidad → ritmo (s/km); en ritmo, más rápido = número menor.
        out["ritmo"] = _rangos([round(umbral_s / f) for f in FRAC_VEL_UMBRAL])
        out["ritmo_umbral_s"] = round(umbral_s)
    return out


def _zona_fc(bpm: float, z: dict) -> int:
    return bisect.bisect_right([r["hasta"] for r in z["fc"][:4]], bpm)


def _zona_ritmo(s_km: float, z: dict) -> int:
    # Fronteras de ritmo de I1/I2 a I4/I5 van bajando (más rápido): contar cuántas supera.
    return sum(s_km < r["hasta"] for r in z["ritmo"][:4])


# ── Una carrera: segundos en cada zona ──────────────────────────────────────

def segundos_por_zona(c: dict, z: dict) -> tuple[list[float], str]:
    """([s en I1..I5], de dónde salió: «fc», «ritmo», «fc_media», «ritmo_medio» o «sin_datos»)."""
    seg = [0.0] * 5
    h = c.get("hist") or {}
    if h.get("fc") and z.get("fc"):
        for bpm, s in h["fc"]:
            seg[_zona_fc(bpm, z)] += s
        return seg, "fc"
    if h.get("ritmo") and z.get("ritmo"):
        for skm, s in h["ritmo"]:
            seg[_zona_ritmo(skm, z)] += s
        return seg, "ritmo"
    t = float(c.get("tiempo_s") or 0)
    if c.get("fc_media") and z.get("fc"):
        seg[_zona_fc(c["fc_media"], z)] = t
        return seg, "fc_media"
    if c.get("distancia_m") and t and z.get("ritmo"):
        seg[_zona_ritmo(t / c["distancia_m"] * 1000, z)] = t
        return seg, "ritmo_medio"
    seg[0] = t
    return seg, "sin_datos"


def estructurada(c: dict) -> bool:
    """Marcada como entrenamiento en Strava o con un nombre que describe series/umbral."""
    return c.get("tipo_entreno") == 3 or bool(PALABRAS_CALIDAD.search(c.get("nombre") or ""))


def tipo_sesion(c: dict, seg: list[float], fuente: str = "fc") -> str:
    """competencia · umbral · intensa · fondo · suave · suave_rapido.

    Una sesión estructurada (por su nombre o la marca de Strava) nunca es un
    rodaje suave: en series con pausas el pulso no alcanza a subir y el tiempo
    en zona se queda corto, y con sólo el promedio de la carrera no hay forma
    de verlo. Basta con algo de I3+ (o no tener el detalle)."""
    total = sum(seg) or 1
    if c.get("tipo_entreno") == 1:
        return "competencia"
    umbral, i5 = seg[2] + seg[3], seg[4]
    if i5 >= I5_MIN_S and i5 >= umbral / 2:
        return "intensa"
    if umbral >= UMBRAL_MIN_S:
        return "umbral"
    if estructurada(c) and (umbral + i5 >= ESTRUCTURADA_MIN_S or fuente not in ("fc", "ritmo")):
        return "intensa" if i5 > umbral else "umbral"
    if (c.get("tiempo_s") or 0) >= 90 * 60 or c.get("tipo_entreno") == 2:
        return "fondo"
    # Un rodaje sin estructura con más de un 20 % sobre el umbral aeróbico:
    # la «zona gris» que el método noruego evita.
    return "suave_rapido" if (umbral + i5) / total > 0.20 else "suave"


def _lunes(fecha: str) -> date:
    d = datetime.fromisoformat(fecha.replace("Z", "")[:19]).date()
    return d - timedelta(days=d.weekday())


# ── Revisión ────────────────────────────────────────────────────────────────

FASES = ("basica", "especifica", "competencia")
# Objetivos por fase: % de tiempo suave (I1–I2), % en I3–I4, % máx. en I5 y
# sesiones de umbral por semana.
OBJETIVOS = {
    "basica": {"suave": (78, 90), "umbral": (8, 18), "i5_max": 3, "sesiones_umbral": (1, 2)},
    "especifica": {"suave": (72, 85), "umbral": (10, 20), "i5_max": 7, "sesiones_umbral": (2, 3)},
    "competencia": {"suave": (72, 88), "umbral": (6, 16), "i5_max": 6, "sesiones_umbral": (1, 2)},
}
PESO_CARGA = (1, 2, 3, 4, 5)        # minutos ponderados por zona (estilo TRIMP de Lucía)


def revision(carreras: list[dict], ajustes: dict, hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    fase = ajustes.get("fase") if ajustes.get("fase") in FASES else "basica"
    lunes_hoy = hoy - timedelta(days=hoy.weekday())
    inicio = lunes_hoy - timedelta(weeks=SEMANAS - 1)
    recientes = [c for c in carreras if c.get("fecha_local") or c.get("fecha")]
    # FC máx. detectada: lo más alto del último año (del detalle si lo hay).
    hace_un_año = (hoy - timedelta(days=365)).isoformat()
    picos = [c.get("hist", {}).get("fc_max") or c.get("fc_max") for c in recientes
             if (c.get("fecha_local") or c.get("fecha") or "") >= hace_un_año]
    z = zonas(ajustes, max((p for p in picos if p and 120 < p < 230), default=None))
    if not z["fc"] and not z["ritmo"]:
        return {"zonas": z, "revision": None}

    semanas = [{"inicio": (inicio + timedelta(weeks=i)).isoformat(), "km": 0.0, "horas": 0.0, "carreras": 0,
                "seg": [0.0] * 5, "carga": 0.0, "umbral": 0, "intensas": 0, "fondo_km": 0.0,
                "suaves": 0, "suaves_rapidos": 0} for i in range(SEMANAS)]
    fuentes: dict[str, float] = {}
    sesiones = []
    for c in recientes:
        f = c.get("fecha_local") or c.get("fecha")
        lunes = _lunes(f)
        if lunes < inicio or lunes > lunes_hoy:
            continue
        seg, fuente = segundos_por_zona(c, z)
        tipo = tipo_sesion(c, seg, fuente)
        s = semanas[(lunes - inicio).days // 7]
        km = (c.get("distancia_m") or 0) / 1000
        s["km"] += km
        s["horas"] += (c.get("tiempo_s") or 0) / 3600
        s["carreras"] += 1
        s["seg"] = [a + b for a, b in zip(s["seg"], seg)]
        s["carga"] += sum(x / 60 * p for x, p in zip(seg, PESO_CARGA))
        s["umbral"] += tipo == "umbral"
        s["intensas"] += tipo == "intensa"
        s["fondo_km"] = max(s["fondo_km"], km)
        s["suaves"] += tipo in ("suave", "suave_rapido")
        s["suaves_rapidos"] += tipo == "suave_rapido"
        fuentes[fuente] = fuentes.get(fuente, 0) + sum(seg)
        sesiones.append({"id": c.get("id"), "fecha": f[:10], "nombre": c.get("nombre", ""), "km": round(km, 1),
                         "tipo": tipo, "fuente": fuente, "pct": _pct(seg)})
    total = [sum(s["seg"][i] for s in semanas) for i in range(5)]
    if not sum(total):
        return {"zonas": z, "revision": {"fase": fase, "vacia": True, "semanas": _semanas_out(semanas)}}

    dist = _pct(total)
    ult4 = semanas[-4:]
    dist4 = _pct([sum(s["seg"][i] for s in ult4) for i in range(5)])
    z1, z2, z3 = (dist[0] + dist[1]) / 100, dist[2] / 100, (dist[3] + dist[4]) / 100
    pi = math.log10(z1 / max(z2, 0.01) * z3 * 100) if z3 > 0 else 0.0
    forma = ("polarizada" if z1 > z3 > z2 and pi > 2 else "piramidal" if z1 > z2 > z3
             else "umbral" if z2 >= z1 else "mixta")
    detalle = round(100 * (fuentes.get("fc", 0) + fuentes.get("ritmo", 0)) / max(sum(fuentes.values()), 1))
    chequeos = _chequeos(semanas, dist, dist4, fase, sesiones)
    return {"zonas": z, "revision": {
        "fase": fase, "desde": inicio.isoformat(), "hasta": hoy.isoformat(),
        "distribucion": dist, "distribucion_4": dist4,
        "tres_zonas": [round(100 * z1), round(100 * z2), round(100 * z3)],
        "polarizacion": round(pi, 2), "forma": forma, "detalle_pct": detalle,
        "fuente": max(fuentes, key=fuentes.get), "semanas": _semanas_out(semanas),
        "sesiones": sorted(sesiones, key=lambda x: x["fecha"], reverse=True)[:40],
        "chequeos": chequeos, "objetivos": OBJETIVOS[fase]}}


def _pct(seg: list[float]) -> list[float]:
    t = sum(seg) or 1
    return [round(100 * x / t, 1) for x in seg]


def _semanas_out(semanas: list[dict]) -> list[dict]:
    return [{"inicio": s["inicio"], "km": round(s["km"], 1), "horas": round(s["horas"], 1),
             "carreras": s["carreras"], "pct": _pct(s["seg"]), "carga": round(s["carga"]),
             "umbral": s["umbral"], "intensas": s["intensas"], "fondo_km": round(s["fondo_km"], 1),
             "suaves_rapidos": s["suaves_rapidos"], "suaves": s["suaves"]} for s in semanas]


def _n(x: float, d: int = 1) -> str:
    """Número con decimales a la chilena (coma) o en inglés (punto)."""
    t = f"{x:.{d}f}"
    return t if en() else t.replace(".", ",")


def _chequeo(clave: str, estado: str, titulo: str, texto: str) -> dict:
    return {"clave": clave, "estado": estado, "titulo": titulo, "texto": texto}


def _chequeos(semanas: list[dict], dist: list[float], dist4: list[float], fase: str,
              sesiones: list[dict]) -> list[dict]:
    obj = OBJETIVOS[fase]
    out = []
    activas = [s for s in semanas if s["carreras"]]
    ult4 = semanas[-4:]
    # 1. Cuánto es suave de verdad
    suave = dist4[0] + dist4[1]
    lo, hi = obj["suave"]
    out.append(_chequeo("suave", "ok" if lo <= suave <= hi else "ojo" if suave >= lo - 8 else "mal",
        tr(f"{suave:.0f} % suave (I1–I2)", f"{suave:.0f} % easy (I1–I2)"),
        tr(f"En esta fase conviene {lo}–{hi} % del tiempo suave. " + (
            "Bien: la base aeróbica se construye aquí." if lo <= suave <= hi else
            "Te falta suave: baja el ritmo de los rodajes para que lo duro rinda." if suave < lo else
            "Casi todo es suave: hay espacio para una sesión de umbral más."),
           f"In this phase aim for {lo}–{hi} % of the time easy. " + (
            "Good: this is where the aerobic base is built." if lo <= suave <= hi else
            "Not enough easy running: slow your easy runs so the hard days pay off." if suave < lo else
            "Almost everything is easy: there's room for one more threshold session."))))
    # 2. Sesiones de umbral por semana (el corazón del método noruego)
    por_sem = sum(s["umbral"] for s in ult4) / 4
    lo, hi = obj["sesiones_umbral"]
    out.append(_chequeo("umbral", "ok" if lo <= por_sem <= hi + 0.5 else "ojo" if por_sem >= lo - 0.75 else "mal",
        tr(f"{_n(por_sem)} sesiones de umbral por semana", f"{_n(por_sem)} threshold sessions per week"),
        tr(f"El método noruego se apoya en {lo}–{hi} sesiones de umbral controlado por semana: intervalos "
           "en I3–I4 (p. ej. 5×6′ o 10×1 km) a un ritmo que podrías repetir, sin terminar fundido.",
           f"The Norwegian method relies on {lo}–{hi} controlled threshold sessions per week: intervals in "
           "I3–I4 (e.g. 5×6′ or 10×1 km) at a pace you could repeat, without finishing wrecked.")))
    # 3. I5
    i5 = dist4[4]
    out.append(_chequeo("i5", "ok" if i5 <= obj["i5_max"] else "ojo" if i5 <= obj["i5_max"] * 2 else "mal",
        tr(f"{i5:.0f} % en I5", f"{i5:.0f} % in I5"),
        tr(f"Hasta {obj['i5_max']} % en esta fase. " + ("Bien dosificado." if i5 <= obj["i5_max"] else
           "Demasiado tiempo muy intenso: cuesta recuperarse y le quita calidad al umbral."),
           f"Up to {obj['i5_max']} % in this phase. " + ("Well dosed." if i5 <= obj["i5_max"] else
           "Too much very hard running: it's costly to recover from and hurts threshold quality."))))
    # 4. Zona gris en los rodajes
    suaves = sum(s["suaves"] for s in ult4)
    rapidos = sum(s["suaves_rapidos"] for s in ult4)
    if suaves:
        p = 100 * rapidos / suaves
        out.append(_chequeo("zona_gris", "ok" if p <= 15 else "ojo" if p <= 35 else "mal",
            tr(f"{p:.0f} % de rodajes suaves demasiado rápidos", f"{p:.0f} % of easy runs too fast"),
            tr("Un rodaje suave debe ser I1–I2 de principio a fin. Correrlo en la «zona gris» cansa casi como "
               "una sesión de calidad y no entrena lo que debería.",
               "An easy run should be I1–I2 from start to finish. Running it in the \"grey zone\" tires you "
               "almost like a quality session without training what it should.")))
    # 5. Carga: últimos 7 días contra el promedio de las 4 semanas anteriores
    previas = [s["carga"] for s in semanas[-5:-1]]
    if any(previas):
        cronica = sum(previas) / 4
        ratio = semanas[-1]["carga"] / cronica if cronica else 0
        if fase == "competencia":
            estado = "ok" if 0.5 <= ratio <= 0.8 else "ojo"
            texto = tr("Antes de competir, la carga baja a 50–70 % manteniendo un par de estímulos cortos e intensos.",
                       "Before racing, load drops to 50–70 % while keeping a couple of short, sharp sessions.")
        else:
            estado = "ok" if 0.8 <= ratio <= 1.3 else "ojo" if ratio <= 1.5 else "mal"
            texto = tr("Entre 0,8 y 1,3 la carga sube a un ritmo asimilable; sobre 1,5 aumenta el riesgo de lesión.",
                       "Between 0.8 and 1.3 load builds at a pace you can absorb; above 1.5 injury risk rises.")
        out.append(_chequeo("carga", estado, tr(f"Carga de esta semana: ×{_n(ratio, 2)}", f"This week's load: ×{_n(ratio, 2)}"),
                            texto))
    # 6. Fondo largo
    largas = [s for s in ult4 if s["km"] >= 15]
    if largas:
        p = max(100 * s["fondo_km"] / s["km"] for s in largas)
        out.append(_chequeo("fondo", "ok" if p <= 35 else "ojo",
            tr(f"Fondo más largo: {p:.0f} % de su semana", f"Longest run: {p:.0f} % of its week"),
            tr("Que el fondo no pase de ~30–35 % del volumen semanal: más que eso se paga en recuperación.",
               "Keep the long run under ~30–35 % of weekly volume: more than that costs recovery.")))
    # 7. Constancia
    if activas:
        semanas_ok = sum(s["carreras"] >= 3 for s in semanas)
        out.append(_chequeo("constancia", "ok" if semanas_ok >= 9 else "ojo" if semanas_ok >= 6 else "mal",
            tr(f"{semanas_ok} de {len(semanas)} semanas con 3+ carreras", f"{semanas_ok} of {len(semanas)} weeks with 3+ runs"),
            tr("La constancia es la base del método: volumen suave repetido semana a semana.",
               "Consistency is the method's foundation: easy volume repeated week after week.")))
    return out
