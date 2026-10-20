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
