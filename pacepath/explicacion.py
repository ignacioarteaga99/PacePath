"""La ruta contada en palabras: por dónde, cuánto, qué hay en el camino.

En español, inglés o portugués según el idioma de la petición (`idioma.tr`).
"""
from __future__ import annotations

from .idioma import en, tr

_VIAS = {
    "primary": ("una avenida principal", "a main avenue", "uma avenida principal"),
    "secondary": ("una avenida", "an avenue", "uma avenida"),
    "tertiary": ("una calle colectora", "a collector street", "uma rua coletora"),
    "residential": ("una calle residencial", "a residential street", "uma rua residencial"),
    "living_street": ("una calle de tráfico calmado", "a traffic-calmed street", "uma rua de trânsito calmo"),
    "pedestrian": ("un paseo peatonal", "a pedestrian promenade", "um calçadão"),
    "footway": ("una senda", "a footpath", "uma trilha"), "path": ("un sendero", "a trail", "uma trilha de terra"),
    "cycleway": ("una ciclovía", "a bike path", "uma ciclovia"), "track": ("un camino de tierra", "a dirt track", "uma estrada de terra"),
    "unclassified": ("una calle", "a street", "uma rua"),
}


def num(x: float, dec: int = 0) -> str:
    """Número con formato chileno (1.234,5) o inglés (1,234.5)."""
    s = f"{x:,.{dec}f}"
    return s if en() else s.replace(",", "§").replace(".", ",").replace("§", ".")


def metros(m: float) -> str:
    return f"{num(m / 1000, 2 if m < 10_000 else 1)} km" if m >= 1000 else f"{num(m)} m"


def lista(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + tr(" y ", " and ",
                                                                     " e ") + xs[-1]


def veces(n: int, es1: str, esN: str, en1: str, enN: str, pt1: str | None = None, ptN: str | None = None) -> str:
    return f"{n} " + (tr(es1, en1, pt1) if n == 1 else tr(esN, enN, ptN))


def _donde(t: dict) -> str:
    if t.get("calles"):
        return lista(t["calles"])
    via = next(iter(t.get("vias", {})), "")
    return tr(*_VIAS.get(via, ("una vía", "a road", "uma via")))


def _sem(n: int) -> str:
    return veces(n, "semáforo", "semáforos", "traffic light", "traffic lights", "semáforo", "semáforos")


def _cru(n: int) -> str:
    return veces(n, "cruce", "cruces", "crossing", "crossings", "cruzamento", "cruzamentos")


def _final(plan: dict, vc: dict | None) -> str:
    """Vuelta a la calma y dónde termina: en la salida, en la meta o donde se cumple."""
    d = metros(vc["distancia_m"]) if vc else metros(plan["totales"].get("vuelta_calma_m", 0))
    if plan.get("volver", True):
        return tr(f"Vuelta a la calma: {d} de vuelta a la salida.",
                  f"Cool-down: {d} back to the start.",
                  f"Desaquecimento: {d} voltando à saída.")
    if plan.get("con_meta"):
        return tr(f"Vuelta a la calma: {d} hasta la meta.", f"Cool-down: {d} to the finish.",
                                                            f"Desaquecimento: {d} até a chegada.")
    calles = (vc or {}).get("calles") or []
    donde = tr(f"; terminas en {calles[-1]}", f"; you finish on {calles[-1]}",
                                              f"; você termina em {calles[-1]}") if calles else ""
    return tr(f"Vuelta a la calma: {d} suaves sin volver a la salida{donde}.",
              f"Cool-down: {d} easy, without returning to the start{donde}.",
              f"Desaquecimento: {d} leve, sem voltar à saída{donde}.")


def explicar_series(plan: dict) -> list[str]:
    t, tot, s = plan["tramo"], plan["totales"], plan["sesion"]
    n = s["repeticiones"]
    total_cruces = t["cruces_menores"] + t["cruces_mayores"]
    if total_cruces == 0:
        cruces = tr("ningún cruce de calle", "no street crossings",
                                             "sem cruzar ruas")
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
           f"{num(t['pendiente_media_pct'], 1)} %).",
           f"Os tiros vão por {_donde(t)}: {metros(t['largo_m'])} "
           f"{'retos' if recto else 'quase retos'}, {_sem(t['semaforos'])}, {cruces}, "
           f"{num(t['desnivel_m'])} m de desnível (inclinação média "
           f"{num(t['pendiente_media_pct'], 1)} %)."),
        tr(f"Haz las {n} repeticiones en ida y vuelta: la 1.ª de ida, la 2.ª de vuelta, y así."
           + (f" Pausa de {s['pausa_s']} s en cada extremo." if s.get("pausa_s") else ""),
           f"Run the {n} reps back and forth: the 1st one out, the 2nd one back, and so on."
           + (f" {s['pausa_s']} s rest at each end." if s.get("pausa_s") else ""),
           f"Faça os {n} tiros indo e voltando: o 1º de ida, o 2º de volta, e assim por diante." + (
           f" {s['pausa_s']} s de descanso em cada ponta." if s.get("pausa_s") else "")),
    ]
    if s.get("escalera_m"):
        lineas[1] = tr("Pirámide: todas las repeticiones salen del inicio del tramo y corren su largo ("
                       + " · ".join(metros(x) for x in s["escalera_m"]) + "); vuelves trotando al inicio"
                       + (f" y descansas {s['pausa_s']} s." if s.get("pausa_s") else "."),
                       "Pyramid: every rep starts at the beginning of the stretch and runs its length ("
                       + " · ".join(metros(x) for x in s["escalera_m"]) + "); jog back to the start"
                       + (f" and rest {s['pausa_s']} s." if s.get("pausa_s") else "."),
                       "Pirâmide: cada tiro começa no início do trecho e percorre seu comprimento (" +
                       " · ".join(metros(x) for x in s["escalera_m"]) + "); volte trotando ao início" +
                       (f" e descanse {s['pausa_s']} s." if s.get("pausa_s") else "."))
    elif s.get("rep_s"):
        lineas[1] = tr(f"Fartlek: {n} cambios de ritmo de unos {metros(t['largo_m'])} (lo que corres en "
                       f"{s['rep_s']} s a ritmo de 5 km), en ida y vuelta por el tramo"
                       + (f", con {s['pausa_s']} s suaves entre uno y otro." if s.get("pausa_s") else "."),
                       f"Fartlek: {n} surges of about {metros(t['largo_m'])} (what you cover in "
                       f"{s['rep_s']} s at 5K pace), back and forth along the stretch"
                       + (f", with {s['pausa_s']} s easy in between." if s.get("pausa_s") else "."),
                       f"Fartlek: {n} acelerações de cerca de {metros(t['largo_m'])} (o que você faz em "
                       f"{s['rep_s']} s no ritmo de 5K), indo e voltando pelo trecho" + (
                       f", com {s['pausa_s']} s leve entre elas." if s.get("pausa_s") else "."))
    if t["parque_pct"] >= 30:
        lineas.append(tr(f"El {t['parque_pct']} % del tramo va por parque.",
                         f"{t['parque_pct']} % of the stretch runs through a park.",
                         f"{t['parque_pct']} % do trecho passa por um parque."))
    cal = next(p for p in plan["partes"] if p["tipo"] == "calentamiento")
    por = (tr(" por ", " along ",
                       " por ") + lista(cal["calles"][:4])) if cal.get("calles") else ""
    n, largo = plan["sesion"]["repeticiones"], plan["tramo"]["largo_m"]
    if s.get("escalera_m"):
        lineas.append(tr(f"Series: {' + '.join(metros(x) for x in s['escalera_m'])} = {metros(tot['series_m'])}.",
                         f"Intervals: {' + '.join(metros(x) for x in s['escalera_m'])} = {metros(tot['series_m'])}.",
                         f"Tiros: {' + '.join(metros(x) for x in s['escalera_m'])} = {metros(tot['series_m'])}."))
    else:
        lineas.append(tr(f"Series: {n} × {metros(largo)} = {metros(tot['series_m'])}.",
                         f"Intervals: {n} × {metros(largo)} = {metros(tot['series_m'])}.",
                         f"Tiros: {n} × {metros(largo)} = {metros(tot['series_m'])}."))
    lineas.append(tr(f"Para llegar: {metros(cal['distancia_m'])} de trote hasta el inicio{por}"
                     f" ({_sem(cal['semaforos'])} en el camino); te sirve de calentamiento.",
                     f"To get there: {metros(cal['distancia_m'])} easy jog to the start{por}"
                     f" ({_sem(cal['semaforos'])} on the way); it doubles as your warm-up.",
                     f"Para chegar: {metros(cal['distancia_m'])} trotando leve até o início{por}"
                     f" ({_sem(cal['semaforos'])} no caminho); serve de aquecimento."))
    return lineas


def explicar_cuestas(plan: dict) -> list[str]:
    t, tot, s = plan["tramo"], plan["totales"], plan["sesion"]
    total_cruces = t["cruces_menores"] + t["cruces_mayores"]
    sem = tr("sin semáforos", "no traffic lights",
                              "sem semáforos") if t["semaforos"] == 0 else _sem(t["semaforos"])
    cru = tr("sin cruces", "no crossings",
                           "sem cruzamentos") if total_cruces == 0 else _cru(total_cruces)
    cal = next(p for p in plan["partes"] if p["tipo"] == "calentamiento")
    por = (tr(" por ", " along ",
                       " por ") + lista(cal["calles"][:4])) if cal.get("calles") else ""
    return [
        tr(f"Las cuestas van por {_donde(t)}: {metros(t['largo_m'])} de subida al "
           f"{num(abs(t['pendiente_pct']), 1)} % (pediste {num(s['pendiente_pct'], 1)} %), "
           f"{sem}, {cru}, {num(t['desnivel_m'])} m de desnivel por repetición.",
           f"The hill reps go up {_donde(t)}: {metros(t['largo_m'])} at "
           f"{num(abs(t['pendiente_pct']), 1)} % (you asked for {num(s['pendiente_pct'], 1)} %), "
           f"{sem}, {cru}, {num(t['desnivel_m'])} m of climb per rep.",
           f"As subidas vão por {_donde(t)}: {metros(t['largo_m'])} a "
           f"{num(abs(t['pendiente_pct']), 1)} % (você pediu {num(s['pendiente_pct'], 1)} %), "
           f"{sem}, {cru}, {num(t['desnivel_m'])} m de subida por tiro."),
        tr(f"Haz las {s['repeticiones']} subidas fuerte y baja trotando suave: la bajada es la "
           "recuperación.",
           f"Run the {s['repeticiones']} climbs hard and jog back down easy: the descent is "
           "your recovery.",
           f"Faça as {s['repeticiones']} subidas forte e desça trotando leve: a descida é "
           "sua recuperação."),
        tr(f"Cuestas: {s['repeticiones']} × {metros(t['largo_m'])} = {metros(tot['series_m'])} "
           "de subida.",
           f"Hills: {s['repeticiones']} × {metros(t['largo_m'])} = {metros(tot['series_m'])} "
           "uphill.",
           f"Subidas: {s['repeticiones']} × {metros(t['largo_m'])} = {metros(tot['series_m'])} "
           "subindo."),
        tr(f"Para llegar: {metros(cal['distancia_m'])} de trote hasta el pie de la cuesta{por}; "
           "te sirve de calentamiento.",
           f"To get there: {metros(cal['distancia_m'])} easy jog to the bottom of the hill{por}; "
           "it doubles as your warm-up.",
           f"Para chegar: {metros(cal['distancia_m'])} trotando leve até o pé da subida{por}; "
           "serve de aquecimento."),
    ]


def _paradas(r: dict) -> str:
    sem, cru = r["semaforos"], r["cruces_mayores"] + r["cruces_menores"]
    if sem == 0 and cru == 0:
        return tr("sin semáforos ni cruces", "no traffic lights or crossings",
                                             "sem semáforos nem cruzamentos")
    return f"{_sem(sem)} {tr('y', 'and',
                                  'e')} {_cru(cru)}"


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
            f"{num(t['desnivel_m'])} m of elevation range.",
            f"O tempo vai por {_donde(t)}: um trecho de {metros(t['largo_m'])} sem semáforos "
            f"nem cruzamentos, ida e volta {v} vezes ({v - 1} retornos), "
            f"{num(t['desnivel_m'])} m de desnível."))
    elif plan.get("forma") == "vueltas":
        b, v = plan["bloque"], plan["vueltas_bloque"]
        lineas.append(tr(
            f"El tempo son {v} vueltas a un circuito de {metros(plan['vuelta_m'])} "
            f"({metros(b['distancia_m'])}): {_paradas(b)}, {num(b['subida_m'])} m de subida. "
            "Calentamiento y vuelta a la calma, por el mismo circuito.",
            f"The tempo is {v} laps of a {metros(plan['vuelta_m'])} loop "
            f"({metros(b['distancia_m'])}): {_paradas(b)}, {num(b['subida_m'])} m of climbing. "
            "Warm-up and cool-down on the same loop.",
            f"O tempo são {v} voltas de um circuito de {metros(plan['vuelta_m'])} "
            f"({metros(b['distancia_m'])}): {_paradas(b)}, {num(b['subida_m'])} m de subida. "
            "Aquecimento e desaquecimento no mesmo circuito."))
    else:
        b = plan["bloque"]
        donde = (tr(" por ", " along ",
                             " por ") + lista(b["calles"][:4])) if b.get("calles") else ""
        lineas.append(tr(
            f"El bloque de tempo es un tramo continuo de {metros(b['distancia_m'])}{donde}: "
            f"{_paradas(b)}, {num(b['subida_m'])} m de subida.",
            f"The tempo block is a continuous {metros(b['distancia_m'])} stretch{donde}: "
            f"{_paradas(b)}, {num(b['subida_m'])} m of climbing.",
            f"O bloco de tempo é um trecho contínuo de {metros(b['distancia_m'])}{donde}: "
            f"{_paradas(b)}, {num(b['subida_m'])} m de subida."))
    alt = plan.get("alternativa")
    if alt:
        otra = {"corredor": tr("corredor", "corridor",
                                           "corredor"), "circuito": tr("circuito", "loop",
                                                                                   "circuito")}.get(
            alt["forma"], alt["forma"])
        lineas.append(tr(
            f"Lo elegí porque tiene menos detenciones probables "
            f"({num(plan['detenciones_bloque'], 1)}) que la otra forma "
            f"({otra}: {num(alt['detenciones_bloque'], 1)}).",
            f"I picked it because it has fewer likely stops "
            f"({num(plan['detenciones_bloque'], 1)}) than the other option "
            f"({otra}: {num(alt['detenciones_bloque'], 1)}).",
            f"Escolhi este porque tem menos paradas prováveis "
            f"({num(plan['detenciones_bloque'], 1)}) que a outra opção "
            f"({otra}: {num(alt['detenciones_bloque'], 1)})."))
    lineas.append(tr(f"Calentamiento: {metros(cal['distancia_m'])}.",
                     f"Warm-up: {metros(cal['distancia_m'])}.",
                     f"Aquecimento: {metros(cal['distancia_m'])}."))
    lineas.append(_final(plan, next((p for p in plan["partes"] if p["tipo"] == "vuelta_calma"), None)))
    lineas.append(tr(f"Total: {metros(tot['distancia_m'])} ({metros(tot['tempo_m'])} de tempo).",
                     f"Total: {metros(tot['distancia_m'])} ({metros(tot['tempo_m'])} at tempo).",
                     f"Total: {metros(tot['distancia_m'])} ({metros(tot['tempo_m'])} em ritmo de tempo)."))
    if tot.get("max_subida_m") is not None:
        lineas.append(tr(f"Desnivel positivo de la sesión: {num(tot['subida_m'])} m (pediste "
                         f"como máximo {num(tot['max_subida_m'])} m).",
                         f"Total climb: {num(tot['subida_m'])} m (you asked for at most "
                         f"{num(tot['max_subida_m'])} m).",
                         f"Subida total: {num(tot['subida_m'])} m (você pediu no máximo "
                         f"{num(tot['max_subida_m'])} m)."))
    return lineas


def explicar_continuo(plan: dict) -> list[str]:
    r, s = plan["recorrido"], plan["sesion"]
    nombre = tr("Fondo", "Long run",
                         "Longão") if s["tipo"] == "fondo" else tr("Regenerativo", "Easy run",
                                                                                     "Regenerativo")
    por = (tr(" por ", " along ",
                       " por ") + lista(r["calles"][:5])) if r.get("calles") else ""
    if r.get("forma") in ("corredor", "favoritos", "parque") and (r.get("corredor") or {}).get("calles"):
        por = f": {lista(r['corredor']['calles'][:4])}"
    formas = {
        "ida y vuelta": ("en ida y vuelta", "out and back", "em ida e volta"),
        "solo ida": ("de solo ida", "one way", "só de ida"),
        "hasta la meta": ("hasta tu meta", "to your finish point", "até a sua chegada"),
        "corredor": ("por un corredor de parques", "along a park corridor", "por um corredor de parques"),
        "parque": ("en vueltas a un parque", "as laps of a park", "em voltas num parque"),
        "favoritos": ("por tus tramos favoritos y calles de siempre",
                      "along your favorite stretches and usual streets",
                      "pelos seus trechos favoritos e ruas de sempre"),
    }
    if r.get("forma") == "vueltas":
        formas["vueltas"] = (f"en {r['vueltas']} vueltas a un circuito de {metros(r['vuelta_m'])}",
                             f"as {r['vueltas']} laps of a {metros(r['vuelta_m'])} loop",
                             f"em {r['vueltas']} voltas num circuito de {metros(r['vuelta_m'])}")
    es, en_, pt_ = formas.get(r.get("forma"), ("en circuito", "loop", "em circuito"))
    lineas = [
        tr(f"{nombre} de {metros(r['distancia_m'])} {es}{por}.",
           f"{metros(r['distancia_m'])} {nombre.lower()} {en_}{por}.",
           f"{nombre} de {metros(r['distancia_m'])} {pt_}{por}."),
        tr(f"En el camino: {_paradas(r)}; {num(r['subida_m'])} m de subida; "
           f"{r['parque_pct']} % por parque.",
           f"On the way: {_paradas(r)}; {num(r['subida_m'])} m of climbing; "
           f"{r['parque_pct']} % through parks.",
           f"No caminho: {_paradas(r)}; {num(r['subida_m'])} m de subida; "
           f"{r['parque_pct']} % por parques."),
    ]
    rep = r.get("vuelta_repetido_pct") if r.get("forma") == "vueltas" else r.get("repetido_pct")
    if r.get("pedida") in ("circuito", "vueltas") and rep is not None:
        donde = tr("cada vuelta", "each lap",
                                  "cada volta") if r.get("forma") == "vueltas" else tr("el circuito",
                                                                                     "the loop",
                                                                                     "o circuito")
        lineas.append(tr(f"{donde[0].upper() + donde[1:]} cierra un círculo sin repetir calles."
                         if rep < 5 else
                         f"{donde[0].upper() + donde[1:]} repite calle en un {rep} % (lo mínimo "
                         "que encontré para cerrar el círculo).",
                         f"{donde[0].upper() + donde[1:]} closes a circle without repeating streets."
                         if rep < 5 else
                         f"{donde[0].upper() + donde[1:]} repeats streets for {rep} % of it (the "
                         "least I could find to close the circle).",
                         f"{donde[0].upper() + donde[1:]} fecha um círculo sem repetir ruas." if rep < 5 else
                         f"{donde[0].upper() + donde[1:]} repete ruas em {rep} % (o "
                         "mínimo que encontrei para fechar o círculo)."))
    if r.get("preferidas_pct"):
        lineas.append(tr(f"Va un {r['preferidas_pct']} % por tus calles de siempre y tramos "
                         "favoritos.",
                         f"{r['preferidas_pct']} % of it is on your usual streets and favorite "
                         "stretches.",
                         f"{r['preferidas_pct']} % fica nas suas ruas de sempre e trechos "
                         "favoritos."))
    tope = plan["totales"].get("max_subida_m")
    if tope is not None:
        lineas.append(tr(f"Desnivel positivo: {num(r['subida_m'])} m (pediste como máximo "
                         f"{num(tope)} m).",
                         f"Total climb: {num(r['subida_m'])} m (you asked for at most "
                         f"{num(tope)} m).",
                         f"Subida total: {num(r['subida_m'])} m (você pediu no máximo "
                         f"{num(tope)} m)."))
    if r.get("forma") in ("corredor", "favoritos") and r.get("corredor"):
        c = r["corredor"]
        llegada = (tr(" por ", " along ",
                               " por ") + lista(c["llegada"][:3])) if c.get("llegada") else ""
        if c["vueltas"] <= 1:
            recorrido = tr("lo recorres en ida y vuelta", "you run it out and back",
                                                          "você vai e volta")
        else:
            recorrido = tr(f"lo recorres de punta a punta {c['vueltas']} veces",
                           f"you run it end to end {c['vueltas']} times",
                           f"você o percorre de ponta a ponta {c['vueltas']} vezes")
        lineas.append(tr(f"Llegas trotando {metros(c['acercamiento_m'])}{llegada}; el corredor "
                         f"mide {metros(c['largo_m'])} y {recorrido}; después vuelves a casa.",
                         f"You jog {metros(c['acercamiento_m'])} to get there{llegada}; the "
                         f"corridor is {metros(c['largo_m'])} long and {recorrido}; then you "
                         "head home.",
                         f"Você trota {metros(c['acercamiento_m'])} para chegar{llegada}; o "
                         f"corredor tem {metros(c['largo_m'])} e {recorrido}; depois "
                         "volta para casa."))
    if r.get("forma") == "parque" and r.get("corredor"):
        c = r["corredor"]
        llegada = (tr(" por ", " along ",
                               " por ") + lista(c["llegada"][:3])) if c.get("llegada") else ""
        vueltas = tr(f"{c['vueltas']} vueltas", f"{c['vueltas']} laps",
                                                f"{c['vueltas']} voltas") if c["vueltas"] > 1 else tr("una vuelta", "one lap",
                                                                                                                  "uma volta")
        if c["acercamiento_m"] < 100:
            lineas.append(tr(f"Sales desde el parque y das {vueltas} a un circuito de {metros(c['largo_m'])} por dentro.",
                             f"You start at the park and run {vueltas} of a {metros(c['largo_m'])} loop inside it.",
                             f"Você sai do parque e dá {vueltas} em um circuito de {metros(c['largo_m'])} dentro dele."))
        else:
            lineas.append(tr(f"Llegas trotando {metros(c['acercamiento_m'])}{llegada}, das {vueltas} a un "
                             f"circuito de {metros(c['largo_m'])} por dentro del parque y vuelves.",
                             f"You jog {metros(c['acercamiento_m'])} to get there{llegada}, run {vueltas} of a "
                             f"{metros(c['largo_m'])} loop inside the park and head back.",
                             f"Você trota {metros(c['acercamiento_m'])} para chegar{llegada}, dá {vueltas} em um "
                             f"circuito de {metros(c['largo_m'])} dentro do parque e volta."))
    if r.get("forma") == "solo ida" and r.get("calles"):
        lineas.append(tr(f"Terminas en {r['calles'][-1]}: revisa cómo volver (metro, micro "
                         "o que te pasen a buscar).",
                         f"You finish on {r['calles'][-1]}: plan how to get back (metro, bus "
                         "or a ride).",
                         f"Você termina em {r['calles'][-1]}: planeje como voltar (metrô, ônibus "
                         "ou carona)."))
    if s["tipo"] == "fondo":
        agua = [a for a in r["agua"] if a["tipo"] == "drinking_water"]
        banos = [a for a in r["agua"] if a["tipo"] == "toilets"]
        if agua:
            # Sin repetir el km (dos fuentes juntas, o la misma de ida y de vuelta).
            kms = list(dict.fromkeys(num(a["a_m"] / 1000, 1) for a in agua))
            lineas.append(tr("Fuentes de agua en los km ", "Water fountains at km ",
                                                           "Bebedouros no km ") + lista(kms) + ".")
        if banos:
            kms = list(dict.fromkeys(num(a["a_m"] / 1000, 1) for a in banos))
            lineas.append(tr("Baños en los km ", "Toilets at km ",
                                                 "Banheiros no km ") + lista(kms) + ".")
    return lineas
