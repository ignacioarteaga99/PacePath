"""La ruta contada en español: por dónde, cuánto, qué hay en el camino."""
from __future__ import annotations

_VIAS = {
    "primary": "avenida principal", "secondary": "avenida", "tertiary": "calle colectora",
    "residential": "calle residencial", "living_street": "calle de tráfico calmado",
    "pedestrian": "paseo peatonal", "footway": "senda", "path": "sendero",
    "cycleway": "ciclovía", "track": "camino de tierra", "unclassified": "calle",
}


def num(x: float, dec: int = 0) -> str:
    """Número con formato chileno: 1.234,5."""
    s = f"{x:,.{dec}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def metros(m: float) -> str:
    return f"{num(m / 1000, 2 if m < 10_000 else 1)} km" if m >= 1000 else f"{num(m)} m"


def lista(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " y " + xs[-1]


def _donde(t: dict) -> str:
    if t.get("calles"):
        return lista(t["calles"])
    via = next(iter(t.get("vias", {})), "")
    return f"una {_VIAS.get(via, 'vía')}"


def explicar_series(plan: dict) -> list[str]:
    t, tot, s = plan["tramo"], plan["totales"], plan["sesion"]
    n = s["repeticiones"]
    sem = "0 semáforos" if t["semaforos"] == 0 else f"{t['semaforos']} semáforo(s)"
    if t["cruces_menores"] == 0 and t["cruces_mayores"] == 0:
        cruces = "ningún cruce de calle"
    else:
        nombres = [c["calle"] for c in t["cruces"] if c.get("calle") and c["coste"] > 0]
        cruces = f"{t['cruces_menores'] + t['cruces_mayores']} cruce(s) de calle"
        if nombres:
            cruces += f" ({lista(sorted(set(nombres)))})"
    lineas = [
        f"Las series van por {_donde(t)}: {metros(t['largo_m'])} "
        f"{'rectos' if t['rectitud'] >= 0.95 else 'casi rectos'}, {sem}, {cruces}, "
        f"desnivel {num(t['desnivel_m'])} m (pendiente media {num(t['pendiente_media_pct'], 1)} %).",
        f"Haz las {n} repeticiones en ida y vuelta: la 1.ª de ida, la 2.ª de vuelta, y así. "
        f"Pausa de {s['pausa_s']} s en cada extremo.",
    ]
    if t["parque_pct"] >= 30:
        lineas.append(f"El {t['parque_pct']} % del tramo va por parque.")
    cal = next(p for p in plan["partes"] if p["tipo"] == "calentamiento")
    vc = next(p for p in plan["partes"] if p["tipo"] == "vuelta_calma")
    por = f" por {lista(cal['calles'][:4])}" if cal.get("calles") else ""
    lineas.append(f"Calentamiento: {metros(cal['distancia_m'])} de trote hasta el inicio{por}"
                  f" ({cal['semaforos']} semáforos en el camino).")
    lineas.append(f"Vuelta a la calma: {metros(vc['distancia_m'])} de vuelta a la salida.")
    lineas.append(f"Total: {metros(tot['distancia_m'])} "
                  f"({metros(tot['series_m'])} de series).")
    return lineas


def explicar_cuestas(plan: dict) -> list[str]:
    t, tot, s = plan["tramo"], plan["totales"], plan["sesion"]
    sem = "sin semáforos" if t["semaforos"] == 0 else f"{t['semaforos']} semáforo(s)"
    cru = "sin cruces" if t["cruces_menores"] + t["cruces_mayores"] == 0 else \
        f"{t['cruces_menores'] + t['cruces_mayores']} cruce(s)"
    cal = next(p for p in plan["partes"] if p["tipo"] == "calentamiento")
    por = f" por {lista(cal['calles'][:4])}" if cal.get("calles") else ""
    return [
        f"Las cuestas van por {_donde(t)}: {metros(t['largo_m'])} de subida al "
        f"{num(abs(t['pendiente_pct']), 1)} % (pediste {num(s['pendiente_pct'], 1)} %), "
        f"{sem}, {cru}, {num(t['desnivel_m'])} m de desnivel por repetición.",
        f"Haz las {s['repeticiones']} subidas fuerte y baja trotando suave: la bajada es la "
        "recuperación.",
        f"Calentamiento: {metros(cal['distancia_m'])} hasta el pie de la cuesta{por}.",
        f"Total: {metros(tot['distancia_m'])} ({metros(tot['series_m'])} de subida).",
    ]


def _paradas(r: dict) -> str:
    sem, cru = r["semaforos"], r["cruces_mayores"] + r["cruces_menores"]
    if sem == 0 and cru == 0:
        return "sin semáforos ni cruces"
    return f"{sem} semáforo(s) y {cru} cruce(s)"


def explicar_tempo(plan: dict) -> list[str]:
    tot, s = plan["totales"], plan["sesion"]
    cal = next(p for p in plan["partes"] if p["tipo"] == "calentamiento")
    lineas = []
    if plan["forma"] == "corredor":
        t = plan["tramo"]
        lineas.append(
            f"El tempo va por {_donde(t)}: un tramo de {metros(t['largo_m'])} sin semáforos ni "
            f"cruces, en ida y vuelta {plan['vueltas']} veces "
            f"({plan['vueltas'] - 1} vueltas en U), desnivel {num(t['desnivel_m'])} m.")
    else:
        b = plan["bloque"]
        donde = f" por {lista(b['calles'][:4])}" if b.get("calles") else ""
        lineas.append(f"El bloque de tempo es un tramo continuo de {metros(b['distancia_m'])}"
                      f"{donde}: {_paradas(b)}, {num(b['subida_m'])} m de subida.")
    alt = plan.get("alternativa")
    if alt:
        lineas.append(f"Lo elegí porque tiene menos detenciones probables "
                      f"({num(plan['detenciones_bloque'], 1)}) que la otra forma "
                      f"({alt['forma']}: {num(alt['detenciones_bloque'], 1)}).")
    lineas.append(f"Calentamiento: {metros(cal['distancia_m'])}. "
                  f"Vuelta a la calma: {metros(tot['vuelta_calma_m'])}.")
    lineas.append(f"Total: {metros(tot['distancia_m'])} ({metros(tot['tempo_m'])} de tempo).")
    return lineas


def explicar_continuo(plan: dict) -> list[str]:
    r, s = plan["recorrido"], plan["sesion"]
    nombre = "Fondo" if s["tipo"] == "fondo" else "Regenerativo"
    por = f" por {lista(r['calles'][:5])}" if r.get("calles") else ""
    forma = {"ida y vuelta": "en ida y vuelta", "solo ida": "de solo ida",
             "hasta la meta": "hasta tu meta"}.get(r.get("forma"), "en circuito")
    lineas = [f"{nombre} de {metros(r['distancia_m'])} {forma}{por}.",
              f"En el camino: {_paradas(r)}; {num(r['subida_m'])} m de subida; "
              f"{r['parque_pct']} % por parque."]
    if r.get("forma") == "solo ida" and r.get("calles"):
        lineas.append(f"Terminas en {r['calles'][-1]}: revisa cómo volver (metro, micro "
                      "o que te pasen a buscar).")
    if s["tipo"] == "fondo":
        agua = [a for a in r["agua"] if a["tipo"] == "drinking_water"]
        banos = [a for a in r["agua"] if a["tipo"] == "toilets"]
        if agua:
            lineas.append("Fuentes de agua en los km " +
                          lista([num(a["a_m"] / 1000, 1) for a in agua]) + ".")
        if banos:
            lineas.append("Baños en los km " +
                          lista([num(a["a_m"] / 1000, 1) for a in banos]) + ".")
    return lineas
