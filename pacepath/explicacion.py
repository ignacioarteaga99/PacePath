"""La ruta contada en palabras: por dónde, cuánto, qué hay en el camino.

En español o inglés según el idioma de la petición (`idioma.tr`).
"""
from __future__ import annotations

from .idioma import en, tr

_VIAS = {
    "primary": ("una avenida principal", "a main avenue"),
    "secondary": ("una avenida", "an avenue"),
    "tertiary": ("una calle colectora", "a collector street"),
    "residential": ("una calle residencial", "a residential street"),
    "living_street": ("una calle de tráfico calmado", "a traffic-calmed street"),
    "pedestrian": ("un paseo peatonal", "a pedestrian promenade"),
    "footway": ("una senda", "a footpath"), "path": ("un sendero", "a trail"),
    "cycleway": ("una ciclovía", "a bike path"), "track": ("un camino de tierra", "a dirt track"),
    "unclassified": ("una calle", "a street"),
}


def num(x: float, dec: int = 0) -> str:
    """Número con formato chileno (1.234,5) o inglés (1,234.5)."""
    s = f"{x:,.{dec}f}"
    return s if en() else s.replace(",", "§").replace(".", ",").replace("§", ".")


def metros(m: float) -> str:
    return f"{num(m / 1000, 2 if m < 10_000 else 1)} km" if m >= 1000 else f"{num(m)} m"


def lista(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + tr(" y ", " and ") + xs[-1]


def veces(n: int, es1: str, esN: str, en1: str, enN: str) -> str:
    return f"{n} " + (tr(es1, en1) if n == 1 else tr(esN, enN))


def _donde(t: dict) -> str:
    if t.get("calles"):
        return lista(t["calles"])
    via = next(iter(t.get("vias", {})), "")
    es, en_ = _VIAS.get(via, ("una vía", "a road"))
    return tr(es, en_)


def _sem(n: int) -> str:
    return veces(n, "semáforo", "semáforos", "traffic light", "traffic lights")


def _cru(n: int) -> str:
    return veces(n, "cruce", "cruces", "crossing", "crossings")


def _final(plan: dict, vc: dict | None) -> str:
    """Vuelta a la calma y dónde termina: en la salida, en la meta o donde se cumple."""
    d = metros(vc["distancia_m"]) if vc else metros(plan["totales"].get("vuelta_calma_m", 0))
    if plan.get("volver", True):
        return tr(f"Vuelta a la calma: {d} de vuelta a la salida.",
                  f"Cool-down: {d} back to the start.")
    if plan.get("con_meta"):
        return tr(f"Vuelta a la calma: {d} hasta la meta.", f"Cool-down: {d} to the finish.")
    calles = (vc or {}).get("calles") or []
    donde = tr(f"; terminas en {calles[-1]}", f"; you finish on {calles[-1]}") if calles else ""
    return tr(f"Vuelta a la calma: {d} suaves sin volver a la salida{donde}.",
              f"Cool-down: {d} easy, without returning to the start{donde}.")


def explicar_series(plan: dict) -> list[str]:
    t, tot, s = plan["tramo"], plan["totales"], plan["sesion"]
    n = s["repeticiones"]
    total_cruces = t["cruces_menores"] + t["cruces_mayores"]
    if total_cruces == 0:
        cruces = tr("ningún cruce de calle", "no street crossings")
    else:
        nombres = [c["calle"] for c in t["cruces"] if c.get("calle") and c["coste"] > 0]
        cruces = _cru(total_cruces)
        if nombres:
            cruces += f" ({lista(sorted(set(nombres)))})"
    recto = t["rectitud"] >= 0.95
    lineas = [
        tr(f"Las series van por {_donde(t)}: {metros(t['largo_m'])} "
           f"{'rectos' if recto else 'casi rectos'}, {_sem(t['semaforos'])}, {cruces}, "
           f"desnivel {num(t['desnivel_m'])} m (pendiente media {num(t['pendiente_media_pct'], 1)} %).",
           f"The intervals go along {_donde(t)}: {metros(t['largo_m'])} "
           f"{'straight' if recto else 'almost straight'}, {_sem(t['semaforos'])}, {cruces}, "
           f"{num(t['desnivel_m'])} m of elevation range (average grade "
           f"{num(t['pendiente_media_pct'], 1)} %)."),
        tr(f"Haz las {n} repeticiones en ida y vuelta: la 1.ª de ida, la 2.ª de vuelta, y así. "
           f"Pausa de {s['pausa_s']} s en cada extremo.",
           f"Run the {n} reps back and forth: the 1st one out, the 2nd one back, and so on. "
           f"{s['pausa_s']} s rest at each end."),
    ]
    if t["parque_pct"] >= 30:
        lineas.append(tr(f"El {t['parque_pct']} % del tramo va por parque.",
                         f"{t['parque_pct']} % of the stretch runs through a park."))
    cal = next(p for p in plan["partes"] if p["tipo"] == "calentamiento")
    vc = next(p for p in plan["partes"] if p["tipo"] == "vuelta_calma")
    por = (tr(" por ", " along ") + lista(cal["calles"][:4])) if cal.get("calles") else ""
    lineas.append(tr(f"Calentamiento: {metros(cal['distancia_m'])} de trote hasta el inicio{por}"
                     f" ({_sem(cal['semaforos'])} en el camino).",
                     f"Warm-up: {metros(cal['distancia_m'])} easy jog to the start{por}"
                     f" ({_sem(cal['semaforos'])} on the way)."))
    lineas.append(_final(plan, vc))
    lineas.append(tr(f"Total: {metros(tot['distancia_m'])} ({metros(tot['series_m'])} de series).",
                     f"Total: {metros(tot['distancia_m'])} ({metros(tot['series_m'])} of intervals)."))
    return lineas


def explicar_cuestas(plan: dict) -> list[str]:
    t, tot, s = plan["tramo"], plan["totales"], plan["sesion"]
    total_cruces = t["cruces_menores"] + t["cruces_mayores"]
    sem = tr("sin semáforos", "no traffic lights") if t["semaforos"] == 0 else _sem(t["semaforos"])
    cru = tr("sin cruces", "no crossings") if total_cruces == 0 else _cru(total_cruces)
    cal = next(p for p in plan["partes"] if p["tipo"] == "calentamiento")
    por = (tr(" por ", " along ") + lista(cal["calles"][:4])) if cal.get("calles") else ""
    return [
        tr(f"Las cuestas van por {_donde(t)}: {metros(t['largo_m'])} de subida al "
           f"{num(abs(t['pendiente_pct']), 1)} % (pediste {num(s['pendiente_pct'], 1)} %), "
           f"{sem}, {cru}, {num(t['desnivel_m'])} m de desnivel por repetición.",
           f"The hill reps go up {_donde(t)}: {metros(t['largo_m'])} at "
           f"{num(abs(t['pendiente_pct']), 1)} % (you asked for {num(s['pendiente_pct'], 1)} %), "
           f"{sem}, {cru}, {num(t['desnivel_m'])} m of climb per rep."),
        tr(f"Haz las {s['repeticiones']} subidas fuerte y baja trotando suave: la bajada es la "
           "recuperación.",
           f"Run the {s['repeticiones']} climbs hard and jog back down easy: the descent is "
           "your recovery."),
        tr(f"Calentamiento: {metros(cal['distancia_m'])} hasta el pie de la cuesta{por}.",
           f"Warm-up: {metros(cal['distancia_m'])} to the bottom of the hill{por}."),
        _final(plan, next(p for p in plan["partes"] if p["tipo"] == "vuelta_calma")),
        tr(f"Total: {metros(tot['distancia_m'])} ({metros(tot['series_m'])} de subida).",
           f"Total: {metros(tot['distancia_m'])} ({metros(tot['series_m'])} uphill)."),
    ]


def _paradas(r: dict) -> str:
    sem, cru = r["semaforos"], r["cruces_mayores"] + r["cruces_menores"]
    if sem == 0 and cru == 0:
        return tr("sin semáforos ni cruces", "no traffic lights or crossings")
    return f"{_sem(sem)} {tr('y', 'and')} {_cru(cru)}"


def explicar_tempo(plan: dict) -> list[str]:
    tot = plan["totales"]
    cal = next(p for p in plan["partes"] if p["tipo"] == "calentamiento")
    lineas = []
    if plan["forma"] == "corredor":
        t = plan["tramo"]
        v = plan["vueltas"]
        lineas.append(tr(
            f"El tempo va por {_donde(t)}: un tramo de {metros(t['largo_m'])} sin semáforos ni "
            f"cruces, en ida y vuelta {v} veces ({v - 1} vueltas en U), "
            f"desnivel {num(t['desnivel_m'])} m.",
            f"The tempo runs along {_donde(t)}: a {metros(t['largo_m'])} stretch with no traffic "
            f"lights or crossings, back and forth {v} times ({v - 1} U-turns), "
            f"{num(t['desnivel_m'])} m of elevation range."))
    else:
        b = plan["bloque"]
        donde = (tr(" por ", " along ") + lista(b["calles"][:4])) if b.get("calles") else ""
        lineas.append(tr(
            f"El bloque de tempo es un tramo continuo de {metros(b['distancia_m'])}{donde}: "
            f"{_paradas(b)}, {num(b['subida_m'])} m de subida.",
            f"The tempo block is a continuous {metros(b['distancia_m'])} stretch{donde}: "
            f"{_paradas(b)}, {num(b['subida_m'])} m of climbing."))
    alt = plan.get("alternativa")
    if alt:
        otra = {"corredor": tr("corredor", "corridor"), "circuito": tr("circuito", "loop")}.get(
            alt["forma"], alt["forma"])
        lineas.append(tr(
            f"Lo elegí porque tiene menos detenciones probables "
            f"({num(plan['detenciones_bloque'], 1)}) que la otra forma "
            f"({otra}: {num(alt['detenciones_bloque'], 1)}).",
            f"I picked it because it has fewer likely stops "
            f"({num(plan['detenciones_bloque'], 1)}) than the other option "
            f"({otra}: {num(alt['detenciones_bloque'], 1)})."))
    lineas.append(tr(f"Calentamiento: {metros(cal['distancia_m'])}.",
                     f"Warm-up: {metros(cal['distancia_m'])}."))
    lineas.append(_final(plan, next((p for p in plan["partes"] if p["tipo"] == "vuelta_calma"), None)))
    lineas.append(tr(f"Total: {metros(tot['distancia_m'])} ({metros(tot['tempo_m'])} de tempo).",
                     f"Total: {metros(tot['distancia_m'])} ({metros(tot['tempo_m'])} at tempo)."))
    if tot.get("max_subida_m") is not None:
        lineas.append(tr(f"Desnivel positivo de la sesión: {num(tot['subida_m'])} m (pediste "
                         f"como máximo {num(tot['max_subida_m'])} m).",
                         f"Total climb: {num(tot['subida_m'])} m (you asked for at most "
                         f"{num(tot['max_subida_m'])} m)."))
    return lineas


def explicar_continuo(plan: dict) -> list[str]:
    r, s = plan["recorrido"], plan["sesion"]
    nombre = tr("Fondo", "Long run") if s["tipo"] == "fondo" else tr("Regenerativo", "Easy run")
    por = (tr(" por ", " along ") + lista(r["calles"][:5])) if r.get("calles") else ""
    if r.get("forma") in ("corredor", "favoritos") and (r.get("corredor") or {}).get("calles"):
        por = f": {lista(r['corredor']['calles'][:4])}"
    formas = {
        "ida y vuelta": ("en ida y vuelta", "out and back"),
        "solo ida": ("de solo ida", "one way"),
        "hasta la meta": ("hasta tu meta", "to your finish point"),
        "corredor": ("por un corredor de parques", "along a park corridor"),
        "favoritos": ("por tus tramos favoritos y calles de siempre",
                      "along your favorite stretches and usual streets"),
    }
    es, en_ = formas.get(r.get("forma"), ("en circuito", "loop"))
    lineas = [
        tr(f"{nombre} de {metros(r['distancia_m'])} {es}{por}.",
           f"{metros(r['distancia_m'])} {nombre.lower()} {en_}{por}."),
        tr(f"En el camino: {_paradas(r)}; {num(r['subida_m'])} m de subida; "
           f"{r['parque_pct']} % por parque.",
           f"On the way: {_paradas(r)}; {num(r['subida_m'])} m of climbing; "
           f"{r['parque_pct']} % through parks."),
    ]
    if r.get("preferidas_pct"):
        lineas.append(tr(f"Va un {r['preferidas_pct']} % por tus calles de siempre y tramos "
                         "favoritos.",
                         f"{r['preferidas_pct']} % of it is on your usual streets and favorite "
                         "stretches."))
    tope = plan["totales"].get("max_subida_m")
    if tope is not None:
        lineas.append(tr(f"Desnivel positivo: {num(r['subida_m'])} m (pediste como máximo "
                         f"{num(tope)} m).",
                         f"Total climb: {num(r['subida_m'])} m (you asked for at most "
                         f"{num(tope)} m)."))
    if r.get("forma") in ("corredor", "favoritos") and r.get("corredor"):
        c = r["corredor"]
        llegada = (tr(" por ", " along ") + lista(c["llegada"][:3])) if c.get("llegada") else ""
        if c["vueltas"] <= 1:
            recorrido = tr("lo recorres en ida y vuelta", "you run it out and back")
        else:
            recorrido = tr(f"lo recorres de punta a punta {c['vueltas']} veces",
                           f"you run it end to end {c['vueltas']} times")
        lineas.append(tr(f"Llegas trotando {metros(c['acercamiento_m'])}{llegada}; el corredor "
                         f"mide {metros(c['largo_m'])} y {recorrido}; después vuelves a casa.",
                         f"You jog {metros(c['acercamiento_m'])} to get there{llegada}; the "
                         f"corridor is {metros(c['largo_m'])} long and {recorrido}; then you "
                         "head home."))
    if r.get("forma") == "solo ida" and r.get("calles"):
        lineas.append(tr(f"Terminas en {r['calles'][-1]}: revisa cómo volver (metro, micro "
                         "o que te pasen a buscar).",
                         f"You finish on {r['calles'][-1]}: plan how to get back (metro, bus "
                         "or a ride)."))
    if s["tipo"] == "fondo":
        agua = [a for a in r["agua"] if a["tipo"] == "drinking_water"]
        banos = [a for a in r["agua"] if a["tipo"] == "toilets"]
        if agua:
            lineas.append(tr("Fuentes de agua en los km ", "Water fountains at km ") +
                          lista([num(a["a_m"] / 1000, 1) for a in agua]) + ".")
        if banos:
            lineas.append(tr("Baños en los km ", "Toilets at km ") +
                          lista([num(a["a_m"] / 1000, 1) for a in banos]) + ".")
    return lineas
