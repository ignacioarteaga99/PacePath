"""Bitácora: de las vueltas (laps) de una carrera de Strava a las series.

Si marcaste lap en cada repetición, Strava tiene el tiempo de cada una. Aquí
se separan las repeticiones de las pausas, del calentamiento y de la vuelta a
la calma, y se arma el bloque (2x3x1000 m, pausas, tiempos) como propuesta:
el corredor la revisa, la corrige y recién ahí se guarda en su bitácora.

Sin plan: las repeticiones son las vueltas claramente más rápidas (el mayor
salto entre ritmos). Con plan («6x1k» del Coach, o lo que escribiste): las
vueltas que calzan con la distancia (o el tiempo) de la repetición. Las
pausas largas entre repeticiones separan las series. Con «marcas» (lo que el
corredor corrigió a mano: rep, pausa, cal, vc) no se adivina nada.

No se guarda nada en el servidor: entra y sale.
"""
from __future__ import annotations

from statistics import median

MARCAS = ("rep", "pausa", "cal", "vc")


def _ritmo(v: dict) -> float | None:
    d, t = v.get("distancia_m") or 0, v.get("tiempo_s") or 0
    return t / d * 1000 if d >= 50 and t > 0 else None


def _redondear(x: float, paso: float) -> float:
    return round(x / paso) * paso


def _reps_por_salto(vueltas: list[dict]) -> list[int]:
    """Las vueltas más rápidas: desde la más rápida, hasta el primer salto
    claro de ritmo (≥ 15 %). Entre repeticiones el ritmo varía poco (unos 400
    a 2:55 y unos 2000 a 3:20 siguen siendo series); después vienen el
    calentamiento, la vuelta a la calma y las pausas, mucho más lentos."""
    ritmos = sorted((r, i) for i, v in enumerate(vueltas) if (r := _ritmo(v)) and (v.get("distancia_m") or 0) >= 100)
    for k in range(1, len(ritmos)):
        if (ritmos[k][0] - ritmos[k - 1][0]) / ritmos[k - 1][0] >= 0.15:
            return sorted(i for _, i in ritmos[:k]) if k >= 2 else []    # una sola vuelta rápida no son series
    return []


def _reps_por_plan(vueltas: list[dict], plan: dict) -> list[int]:
    """Las vueltas que calzan con la repetición del plan (±15 %), las más rápidas si sobran."""
    n = int(plan.get("series") or 1) * int(plan.get("reps") or 0)
    if plan.get("dist_m"):
        calza = [i for i, v in enumerate(vueltas) if abs((v.get("distancia_m") or 0) - plan["dist_m"]) <= 0.15 * plan["dist_m"]]
    elif plan.get("dur_s"):
        calza = [i for i, v in enumerate(vueltas) if abs((v.get("tiempo_s") or 0) - plan["dur_s"]) <= 0.15 * plan["dur_s"]]
    else:
        return []
    if len(calza) > n > 0:                       # sobran (p. ej. trotes de 1 km entre series): las más rápidas
        calza = sorted(sorted(calza, key=lambda i: _ritmo(vueltas[i]) or 1e9)[:n])
    return calza if len(calza) >= 2 else []


def proponer(vueltas: list[dict], plan: dict | None = None, marcas: list[str] | None = None) -> dict:
    """Las series que salen de las vueltas: {bloques, marcas, calentamiento_km,
    vuelta_calma_km, vueltas}. Sin series claras, bloques vacío."""
    vueltas = [{"distancia_m": float(v.get("distancia_m") or 0), "tiempo_s": float(v.get("tiempo_s") or 0),
                "fc": v.get("fc")} for v in vueltas]
    if marcas and len(marcas) == len(vueltas) and all(m in MARCAS for m in marcas):
        reps = [i for i, m in enumerate(marcas) if m == "rep"]
    else:
        reps = (_reps_por_plan(vueltas, plan) if plan else []) or _reps_por_salto(vueltas)
        marcas = None
    if not marcas:
        marcas = ["pausa"] * len(vueltas)
        for i in reps:
            marcas[i] = "rep"
        if reps:
            for i in range(reps[0]):
                marcas[i] = "cal"
            for i in range(reps[-1] + 1, len(vueltas)):
                marcas[i] = "vc"
    km = lambda tipo: round(sum(v["distancia_m"] for v, m in zip(vueltas, marcas) if m == tipo) / 1000, 1)  # noqa: E731
    out = {"marcas": marcas, "calentamiento_km": km("cal"), "vuelta_calma_km": km("vc"), "bloques": [],
           "vueltas": [{"i": i + 1, "distancia_m": round(v["distancia_m"]), "tiempo_s": round(v["tiempo_s"], 1),
                        "ritmo_s": round(r) if (r := _ritmo(v)) else None, "fc": v["fc"]} for i, v in enumerate(vueltas)]}
    if len(reps) < 2:
        return out
    # Bloques: cada vez que cambia la distancia de la repetición (3x2000 + 5x400), uno nuevo.
    grupos = [[reps[0]]]
    for i in reps[1:]:
        ref = vueltas[grupos[-1][0]]["distancia_m"]
        if abs(vueltas[i]["distancia_m"] - ref) <= 0.2 * ref:
            grupos[-1].append(i)
        else:
            grupos.append([i])
    out["bloques"] = [_bloque(vueltas, g, plan if len(grupos) == 1 else None) for g in grupos]
    return out


def _bloque(vueltas: list[dict], reps: list[int], plan: dict | None) -> dict:
    """Un bloque (series × reps × distancia o tiempo, pausas y tiempos) con sus repeticiones."""
    if len(reps) == 1:
        v = vueltas[reps[0]]
        return {"series": 1, "reps": 1, "dist_m": int(_redondear(v["distancia_m"], 50 if v["distancia_m"] < 1000 else 100)),
                "dur_s": None, "obj": None, "p_rep": None, "p_serie": None, "tiempos": [[round(v["tiempo_s"], 1)]]}
    # Pausa entre cada repetición y la siguiente: lo que hay entre ellas.
    pausas = [sum(vueltas[j]["tiempo_s"] for j in range(a + 1, b)) for a, b in zip(reps, reps[1:])]
    med = median(pausas)
    cortes = [k for k, p in enumerate(pausas) if p > 1.6 * med and p - med >= 30]
    tamaños, ini = [], 0
    for k in cortes:
        tamaños.append(k + 1 - ini)
        ini = k + 1
    tamaños.append(len(reps) - ini)
    iguales = len(set(tamaños)) == 1
    series, por_serie = (len(tamaños), tamaños[0]) if iguales else (1, len(reps))
    # ¿Por distancia o por tiempo? Si las distancias varían y los tiempos no, es por tiempo (6x3').
    dist = [vueltas[i]["distancia_m"] for i in reps]
    tiem = [vueltas[i]["tiempo_s"] for i in reps]
    cv = lambda xs: (max(xs) - min(xs)) / median(xs) if median(xs) else 1  # noqa: E731
    # Por tiempo (6x3'): el reloj marcó las vueltas por tiempo, así que todas duran casi lo mismo (y redondo),
    # y la distancia no es una de pista. Con lap a mano y GPS, ambas varían un poco: es por distancia.
    redonda = any(abs(median(dist) - r) <= 0.03 * r for r in (100, 150, 200, 300, 400, 500, 600, 800, 1000, 1200, 1500, 1600, 2000, 3000, 4000, 5000))
    por_tiempo = bool(plan and plan.get("dur_s")) or (not (plan and plan.get("dist_m")) and cv(tiem) < 0.02
                                                      and abs(median(tiem) - _redondear(median(tiem), 15)) <= 2 and not redonda)
    b = {"series": series, "reps": por_serie, "dist_m": None, "dur_s": None, "obj": (plan or {}).get("obj"),
         "p_rep": None, "p_serie": None, "tiempos": []}
    if por_tiempo:
        b["dur_s"] = (plan or {}).get("dur_s") or int(_redondear(median(tiem), 15))
        valores = [round(d) for d in dist]
    else:
        m = median(dist)
        plan_d = (plan or {}).get("dist_m")
        b["dist_m"] = int(plan_d if plan_d and abs(m - plan_d) <= 0.15 * plan_d else _redondear(m, 50 if m < 1000 else 100))
        valores = [round(x, 1) for x in tiem]
    b["tiempos"] = [valores[s * por_serie:(s + 1) * por_serie] for s in range(series)]
    dentro = [p for k, p in enumerate(pausas) if k not in cortes] if iguales else pausas
    entre = [pausas[k] for k in cortes] if iguales else []
    b["p_rep"] = int(_redondear(median(dentro), 5)) if dentro and median(dentro) > 0 else None
    b["p_serie"] = int(_redondear(median(entre), 5)) if entre else None
    return b
