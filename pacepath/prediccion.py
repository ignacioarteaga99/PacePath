"""Predictor del Coach: el tiempo probable en la carrera objetivo, hoy y el día
de la carrera si se sigue el plan, siempre como rango.

1. El nivel de hoy (VDOT de Daniels), de lo más confiable a lo menos:
   - una carrera de las últimas 12 semanas (marcada como carrera en Strava);
   - los entrenamientos: cualquier salida de 5 km o más da un piso (nadie
     corre más rápido de lo que puede); se usa tal cual, sin suponer que
     faltó esfuerzo (un fondo o un tempo fuerte ya está cerca de lo máximo);
   - los mejores tiempos escritos en Entreno.
2. Ajuste por distancia: las tablas suponen entrenar para la distancia. En la
   maratón (y algo en la media) poco volumen semanal o un fondo corto se
   pagan al final; se agrega un porcentaje según los km por semana y el fondo
   más largo de las últimas 8 semanas (la idea del modelo de Vickers y
   Vertosick, 2016, con números conservadores propios).
3. Proyección: si se sigue el plan, el nivel sube poco a poco (menos mientras
   más alto es; a lo más ~3 VDOT) y el afinamiento da ~1,5 %.
4. El rango: ±1 % con una carrera reciente, ±2,5 % con los mejores tiempos,
   ±3 % sólo con entrenamientos; más ancho mientras más lejos está la carrera,
   y más angosto con evidencia de que el plan se está cumpliendo: sesiones
   de calidad hechas (−0,1 % c/u, hasta −0,8 %), fondos largos completados
   (−0,25 % c/u, hasta −0,75 %) y un cumplimiento del 80 % o más (−0,5 %; si
   es bajo, +0,5 %). Nunca baja de ±1,5 % sin una carrera ni de ±1 % con ella.

No sabe el clima, el recorrido, cómo se dosificará ni el estómago: por eso es
un rango y la app lo dice.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import entreno
import statistics

from .entreno import ritmo_umbral_de_vdot, segundos_por_zona, tiempo_equivalente, tipo_sesion, vdot, zonas
from .idioma import en, tr

VENTANA_DIAS = 84
GANANCIA_MAX = 3.0
AFINE = 0.985
DIST_TXT = {"5k": "5K", "10k": "10K", "21k": ("media maratón", "half marathon", "meia maratona"),
            "42k": ("maratón", "marathon", "maratona")}


def _fecha(c: dict) -> str:
    return (c.get("fecha_local") or c.get("fecha") or "")[:10]


def _f(x: float, d: int = 1) -> str:
    s = f"{x:.{d}f}"
    return s if en() else s.replace(".", ",")


def hms(s: float) -> str:
    s = round(s)
    h, m, x = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m}:{x:02d}"


def _dia(d: str) -> str:
    f = date.fromisoformat(d)
    meses = (["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
             ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
             ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"])
    m = tr(*[x[f.month - 1] for x in meses])
    return f"{m} {f.day}" if en() else f"{f.day} {m}"


def _nombre_dist(d_m: float) -> str:
    for k, v in entreno.DISTANCIAS.items():
        if abs(d_m - v) / v < 0.04:
            t = DIST_TXT[k]
            return t if isinstance(t, str) else tr(*t)
    return f"{_f(d_m / 1000)} km"


def nivel(carreras: list[dict], ajustes: dict, hasta: date) -> dict | None:
    """VDOT de hoy, de dónde sale y cuánto confiar (fracción del rango)."""
    desde = (hasta - timedelta(days=VENTANA_DIAS)).isoformat()
    recientes = [c for c in carreras if desde <= _fecha(c) < hasta.isoformat()
                 and (c.get("distancia_m") or 0) >= 3000 and (c.get("tiempo_s") or 0) > 0]
    carreras_ok = [(vdot(c["distancia_m"], c["tiempo_s"]), c) for c in recientes if c.get("tipo_entreno") == 1]
    piso = max((vdot(c["distancia_m"], c["tiempo_s"]) for c in recientes if c["distancia_m"] >= 5000), default=None)
    if carreras_ok:
        v, c = max(carreras_ok, key=lambda x: x[0])
        return {"vdot": max(v, piso or 0), "fuente": "carrera", "conf": 0.01,
                "detalle": tr(f"tu {_nombre_dist(c['distancia_m'])} del {_dia(_fecha(c))} ({hms(c['tiempo_s'])})",
                              f"your {_nombre_dist(c['distancia_m'])} on {_dia(_fecha(c))} ({hms(c['tiempo_s'])})",
                              f"sua {_nombre_dist(c['distancia_m'])} de {_dia(_fecha(c))} ({hms(c['tiempo_s'])})")}
    tiempos = zonas(ajustes).get("vdot")
    entreno_v = piso
    if tiempos and (not entreno_v or tiempos >= entreno_v):
        return {"vdot": tiempos, "fuente": "tiempos", "conf": 0.025,
                "detalle": tr("tus mejores tiempos de Entreno", "your best times in Training", "seus melhores tempos em Treino")}
    if entreno_v:
        return {"vdot": entreno_v, "fuente": "entreno", "conf": 0.03,
                "detalle": tr("tus entrenamientos de las últimas 12 semanas", "your training over the last 12 weeks",
                              "seus treinos das últimas 12 semanas")}
    return None


def _volumen(carreras: list[dict], hasta: date, semanas: int = 8) -> tuple[float, float]:
    """(km por semana promedio, fondo más largo) de las últimas `semanas`."""
    desde = (hasta - timedelta(weeks=semanas)).isoformat()
    sel = [c for c in carreras if desde <= _fecha(c) < hasta.isoformat()]
    km = sum((c.get("distancia_m") or 0) for c in sel) / 1000
    return km / semanas, max(((c.get("distancia_m") or 0) / 1000 for c in sel), default=0.0)


def ajuste_distancia(obj: str, km_sem: float, largo: float) -> tuple[float, list[str]]:
    """(factor sobre el tiempo, por qué). Sólo media y maratón."""
    reglas = {"42k": (70, 0.10, 0.08, 30, 0.08, 0.04), "21k": (40, 0.05, 0.03, 16, 0.04, 0.02)}
    if obj not in reglas:
        return 1.0, []
    vol_ok, vol_k, vol_max, largo_ok, largo_k, largo_max = reglas[obj]
    pen, motivos = 0.0, []
    if km_sem < vol_ok:
        p = min(vol_max, (vol_ok - km_sem) / vol_ok * vol_k)
        pen += p
        motivos.append(tr(f"Con {km_sem:.0f} km por semana la predicción suma ~{_f(100 * p)} %: más volumen la bajaría.",
                          f"At {km_sem:.0f} km per week the prediction adds ~{_f(100 * p)} %: more volume would lower it.",
                          f"Com {km_sem:.0f} km por semana a previsão soma ~{_f(100 * p)} %: mais volume a baixaria."))
    if largo < largo_ok:
        p = min(largo_max, (largo_ok - largo) / largo_ok * largo_k)
        pen += p
        motivos.append(tr(f"Tu fondo más largo es de {largo:.0f} km: uno de {largo_ok}+ km acortaría el final (~{_f(100 * p)} %).",
                          f"Your longest run is {largo:.0f} km: a {largo_ok}+ km one would help the finish (~{_f(100 * p)} %).",
                          f"Seu longão mais longo é de {largo:.0f} km: um de {largo_ok}+ km ajudaria o final (~{_f(100 * p)} %)."))
    return 1 + pen, motivos


FONDO_CLAVE = {"42k": 30, "21k": 16, "10k": 14, "5k": 10}      # km de un fondo que cuenta como evidencia


def confianza(base: float, fuente: str, evidencia: dict) -> tuple[float, list[str]]:
    """El ± del rango de hoy, angostado (o ensanchado) por lo que se ha cumplido."""
    calidad, fondos, cumpl = evidencia.get("calidad", 0), evidencia.get("fondos", 0), evidencia.get("cumplimiento")
    c, por = base, []
    if calidad:
        c -= min(0.008, 0.001 * calidad)
        por.append(tr(f"{calidad} sesiones de calidad cumplidas", f"{calidad} quality sessions done",
                      f"{calidad} sessões de qualidade cumpridas") if calidad > 1 else
                   tr("1 sesión de calidad cumplida", "1 quality session done", "1 sessão de qualidade cumprida"))
    if fondos:
        c -= min(0.0075, 0.0025 * fondos)
        por.append(tr(f"{fondos} fondos largos", f"{fondos} long runs", f"{fondos} longões") if fondos > 1 else
                   tr("1 fondo largo", "1 long run", "1 longão"))
    if cumpl is not None and cumpl >= 0.8:
        c -= 0.005
        por.append(tr(f"{round(100 * cumpl)} % del plan cumplido", f"{round(100 * cumpl)} % of the plan done",
                      f"{round(100 * cumpl)} % do plano cumprido"))
    elif cumpl is not None and cumpl < 0.5:
        c += 0.005
    return max(0.01 if fuente == "carrera" else 0.015, c), por


def prediccion(perfil: dict, carreras: list[dict], ajustes: dict, hoy: date, plan_semanas: list[dict],
               cumplimiento: float | None, evidencia: dict | None = None) -> dict | None:
    obj = perfil.get("objetivo")
    if obj not in entreno.DISTANCIAS or not perfil.get("fecha"):
        return None
    dist = entreno.DISTANCIAS[obj]
    n = nivel(carreras, ajustes, hoy)
    if not n:
        return None
    km_sem, largo = _volumen(carreras, hoy)
    factor, motivos = ajuste_distancia(obj, km_sem, largo)
    t_hoy = tiempo_equivalente(n["vdot"], dist) * factor
    conf, por_que = confianza(n["conf"], n["fuente"], (evidencia or {}) | {"cumplimiento": cumplimiento})
    # El día de la carrera, si se sigue el plan.
    carrera = date.fromisoformat(perfil["fecha"])
    semanas = max(0, (carrera - hoy).days // 7)
    ritmo_sube = 0.25 * min(1.0, max(0.15, (72 - n["vdot"]) / 30))
    if cumplimiento is not None and cumplimiento < 0.6:
        ritmo_sube /= 2
    ganancia = min(GANANCIA_MAX, ritmo_sube * max(0, semanas - 2))
    futuras = [w for w in plan_semanas if w["lunes"] >= hoy.isoformat() and not w.get("descarga")]
    km_plan = max([km_sem] + [w["km"] for w in futuras])
    largo_plan = max([largo] + [w.get("fondo_km", 0) for w in futuras])
    factor_dia, _ = ajuste_distancia(obj, km_plan, largo_plan)
    t_dia = tiempo_equivalente(n["vdot"] + ganancia, dist) * factor_dia * (AFINE if semanas >= 2 else 1)
    conf_dia = min(0.04, conf + 0.005 * semanas / 4)
    rango = lambda t, c: [round(t * (1 - c)), round(t * (1 + c))]  # noqa: E731
    out = {"distancia": obj, "hoy": rango(t_hoy, conf), "dia": rango(t_dia, conf_dia), "fecha": perfil["fecha"],
           "conf_pct": round(100 * conf, 1), "conf_dia_pct": round(100 * conf_dia, 1), "por_que_rango": por_que,
           "fuente": n["fuente"], "detalle": n["detalle"], "vdot": round(n["vdot"], 1), "motivos": motivos,
           "ritmo_dia_s": round(t_dia / (dist / 1000))}
    # Historia: cómo ha cambiado la predicción de hoy, semana a semana.
    lunes = hoy - timedelta(days=hoy.weekday())
    hist = []
    for i in range(8, 0, -1):
        f = lunes - timedelta(weeks=i)
        ni = nivel(carreras, ajustes, f)
        if ni:
            ks, lg = _volumen(carreras, f)
            hist.append({"lunes": f.isoformat(), "t": round(tiempo_equivalente(ni["vdot"], dist) * ajuste_distancia(obj, ks, lg)[0])})
    hist.append({"lunes": lunes.isoformat(), "t": round(t_hoy)})
    out["historia"] = hist
    if n["fuente"] != "carrera":
        # Cuándo conviene la carrera de prueba: 6–10 semanas antes de una maratón (un 21K), 3–6 antes de
        # una media (un 10K); en 5K y 10K, cualquier carrera corta sirve.
        ventana = {"42k": (6, 10, "21K"), "21k": (3, 6, "10K")}.get(obj)
        if ventana and ventana[0] <= semanas <= ventana[1]:
            out["motivos"].append(tr(f"Buen momento para un {ventana[2]} de prueba: afinaría la predicción y te daría el ritmo real.",
                                     f"Good time for a tune-up {ventana[2]}: it would sharpen the prediction and give you your real pace.",
                                     f"Bom momento para uma {ventana[2]} de teste: afinaria a previsão e daria seu ritmo real."))
        else:
            out["motivos"].append(tr("Una carrera de prueba (5K a 21K) haría la predicción mucho más precisa"
                                     + (f": la ideal, un {ventana[2]} unas {ventana[0]}–{ventana[1]} semanas antes." if ventana else "."),
                                     "A tune-up race (5K to 21K) would make the prediction much more precise"
                                     + (f": ideally a {ventana[2]} about {ventana[0]}–{ventana[1]} weeks out." if ventana else "."),
                                     "Uma prova de teste (5K a 21K) deixaria a previsão bem mais precisa"
                                     + (f": o ideal, uma {ventana[2]} umas {ventana[0]}–{ventana[1]} semanas antes." if ventana else ".")))
    # Contra el tiempo objetivo.
    meta = perfil.get("tiempo_objetivo_s")
    if meta:
        centro = t_dia
        if centro <= meta:
            estado = "en_camino" if centro >= meta * 0.95 else "conservador"
        else:
            estado = "exigente" if centro <= meta * 1.03 else "muy_exigente"
        out["objetivo"] = {"t": meta, "estado": estado, "ritmo_s": round(meta / (dist / 1000)),
                           "texto": _texto_objetivo(estado, meta, centro)}
    return out


def _texto_objetivo(estado: str, meta: float, centro: float) -> str:
    dif = abs(centro - meta)
    return {
        "en_camino": tr("Vas en camino: si sigues el plan, tu objetivo está a tu alcance.",
                        "You're on track: if you follow the plan, your goal is within reach.",
                        "Você está no caminho: seguindo o plano, seu objetivo está ao seu alcance."),
        "conservador": tr(f"Tu objetivo parece conservador: la predicción es ~{hms(dif)} más rápida. Puedes apuntar más alto.",
                          f"Your goal looks conservative: the prediction is ~{hms(dif)} faster. You could aim higher.",
                          f"Seu objetivo parece conservador: a previsão é ~{hms(dif)} mais rápida. Dá para mirar mais alto."),
        "exigente": tr(f"Exigente: hoy la predicción está ~{hms(dif)} sobre tu objetivo. Con el plan bien hecho, se puede.",
                       f"Demanding: the prediction is ~{hms(dif)} over your goal. With the plan done well, it's doable.",
                       f"Exigente: hoje a previsão está ~{hms(dif)} acima do objetivo. Com o plano bem feito, dá."),
        "muy_exigente": tr(f"Muy exigente: la predicción está ~{hms(dif)} sobre tu objetivo. Considera ajustarlo para no "
                           "salir demasiado rápido el día de la carrera.",
                           f"Very demanding: the prediction is ~{hms(dif)} over your goal. Consider adjusting it so you "
                           "don't go out too fast on race day.",
                           f"Muito exigente: a previsão está ~{hms(dif)} acima do objetivo. Considere ajustá-lo para não "
                           "sair rápido demais no dia da prova."),
    }[estado]


# ── Predicción teórica por el ritmo umbral ─────────────────────────────────
# Lo que dicen las tablas de Daniels para el ritmo umbral (el T) que se viene
# sosteniendo, en todas las distancias y sin ajustar por volumen ni fondos: lo
# que podrías correr si entrenaras para cada una. El umbral sale de las
# sesiones de umbral de las últimas 8 semanas (lo más rápido que se sostuvo 20
# min en cada una, la mediana); sin ese detalle, el de tus zonas.
UMBRAL_TRAMO_S = 20 * 60


def umbral_entrenado(carreras: list[dict], z: dict, hoy: date) -> tuple[float | None, int]:
    """(s/km del umbral sostenido en tus sesiones de umbral, cuántas sesiones)."""
    desde = (hoy - timedelta(weeks=8)).isoformat()
    ritmos = []
    for c in carreras:
        h = (c.get("hist") or {}).get("ritmo")
        if not h or not (desde <= _fecha(c) <= hoy.isoformat()) or not (z.get("fc") or z.get("ritmo")):
            continue
        seg, fuente = segundos_por_zona(c, z)
        if tipo_sesion(c, seg, fuente) != "umbral":          # series en I5 serían más rápidas que el umbral
            continue
        acum = suma = 0.0
        for r, sg in sorted(h):                               # de lo más rápido a lo más lento
            tramo = min(sg, UMBRAL_TRAMO_S - acum)
            suma += r * tramo
            acum += tramo
            if acum >= UMBRAL_TRAMO_S:
                break
        if acum >= UMBRAL_TRAMO_S:
            ritmos.append(suma / acum)
    return (statistics.median(ritmos), len(ritmos)) if ritmos else (None, 0)


def vdot_de_umbral(s_km: float) -> float:
    """La inversa de ritmo_umbral_de_vdot (bisección: más VDOT, umbral más rápido)."""
    lo, hi = 20.0, 90.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if ritmo_umbral_de_vdot(mid) > s_km:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def teorica(carreras: list[dict], z: dict, hoy: date) -> dict | None:
    umbral, n = umbral_entrenado(carreras, z, hoy)
    if umbral:
        fuente = tr(f"tu umbral en {n} sesiones de las últimas 8 semanas", f"your threshold in {n} sessions over the last 8 weeks",
                    f"seu limiar em {n} sessões das últimas 8 semanas") if n > 1 else \
            tr("tu umbral en tu última sesión de umbral", "your threshold in your last threshold session",
               "seu limiar na sua última sessão de limiar")
    elif z.get("ritmo_umbral_s"):
        umbral = z["ritmo_umbral_s"]
        fuente = tr("el ritmo umbral de tus zonas (sin sesiones de umbral con detalle todavía)",
                    "the threshold pace from your zones (no detailed threshold sessions yet)",
                    "o ritmo de limiar das suas zonas (ainda sem sessões de limiar com detalhe)")
    else:
        return None
    v = vdot_de_umbral(umbral)
    return {"umbral_s": round(umbral), "vdot": round(v, 1), "fuente": fuente, "de_sesiones": bool(n),
            "tiempos": {k: round(tiempo_equivalente(v, d)) for k, d in entreno.DISTANCIAS.items()}}
