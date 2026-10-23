"""Banco de pruebas de calidad de rutas.

Arma las mismas rutas que pide la app (misma elección de comuna, mismo
planificador) desde salidas reales de Santiago y mide lo que importa:

  · series y cuestas: semáforos y cruces en el tramo, cuánto hay que trotar
    para llegar, error de pendiente en las cuestas.
  · tempo, fondo y regenerativo: error de distancia, semáforos y cruces por km,
    calles repetidas, subida (y si se pasa del tope en «plano»), parques.
  · todas: cuánto tardó y si falló («No encontré…»).

Uso:
    python scripts/banco.py                     # todo, guarda banco/resultados/<fecha>.json
    python scripts/banco.py --etiqueta base     # con nombre
    python scripts/banco.py --comparar base     # y compara con banco/resultados/base.json
    python scripts/banco.py --rapido            # 6 salidas × 4 sesiones (para probar)

Lo corre también el workflow «Banco de rutas» (GitHub Actions).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pacepath.comunas import comuna_para, contexto  # noqa: E402
from pacepath.planificador import planificar  # noqa: E402
from pacepath.series import SinTramo  # noqa: E402
from pacepath.sesion import interpretar  # noqa: E402

# Salidas reales: plazas, parques y esquinas típicas, varias cerca de los
# bordes entre comunas (donde el grafo de la comuna se acaba).
SALIDAS = [
    ("Providencia centro", -33.4258, -70.6100),
    ("Pedro de Valdivia / Bilbao", -33.4330, -70.6000),
    ("Manuel Montt", -33.4290, -70.6200),
    ("Parque Inés de Suárez", -33.4370, -70.6040),
    ("Plaza de Armas", -33.4378, -70.6505),
    ("Barrio Brasil", -33.4440, -70.6640),
    ("Parque O'Higgins", -33.4640, -70.6610),
    ("Bellas Artes", -33.4360, -70.6430),
    ("Escuela Militar", -33.4140, -70.5840),
    ("Parque Araucano", -33.4030, -70.5730),
    ("Los Dominicos", -33.4080, -70.5420),
    ("Plaza Ñuñoa", -33.4560, -70.5970),
    ("Estadio Nacional", -33.4640, -70.6100),
    ("Irarrázaval / Ramón Cruz", -33.4540, -70.5800),
    ("Parque Bicentenario", -33.3990, -70.6010),
    ("Alonso de Córdova", -33.3980, -70.5900),
    ("Plaza Italia (borde)", -33.4370, -70.6345),
    ("Tobalaba (borde)", -33.4180, -70.6010),
]
# (texto, forma)
SESIONES = [
    ("6x1k", "auto"), ("8x400", "auto"), ("cuestas 6x300 6%", "auto"),
    ("tempo 8k", "auto"), ("fondo 15k", "auto"), ("fondo 10k plano", "auto"),
    ("regenerativo 6k", "auto"), ("fondo 10k", "circuito"),
]
RAPIDO_SALIDAS = [0, 4, 8, 11, 14, 16]
RAPIDO_SESIONES = [0, 2, 4, 5]


def medir(plan: dict, texto: str) -> dict:
    s = plan["sesion"]
    m: dict = {}
    if "tramo" in plan:                                    # series y cuestas
        t = plan["tramo"]
        m.update(semaforos_tramo=t["semaforos"], cruces_tramo=t["cruces_mayores"] + t["cruces_menores"],
                 acercamiento_m=t["acercamiento_m"], rectitud=t["rectitud"],
                 semaforos_trote=plan["totales"].get("semaforos_trote", 0))
        if s.get("pendiente_pct"):
            m["error_pendiente"] = round(abs(t["pendiente_media_pct"] - s["pendiente_pct"]), 2)
        return m
    r = plan.get("recorrido") or plan["bloque"]              # tempo: el bloque rápido
    km = max(r["distancia_m"] / 1000, 0.1)
    objetivo = s.get("distancia_m") or r["distancia_m"]
    m.update(error_dist_pct=round(100 * (r["distancia_m"] - objetivo) / objetivo, 1),
             semaforos_km=round(r["semaforos"] / km, 2), cruces_km=round(r["cruces_mayores"] / km, 2),
             repetido_pct=r["repetido_pct"], subida_m=r["subida_m"], subida_km=round(r["subida_m"] / km, 1),
             parque_pct=r["parque_pct"], forma_ruta=r.get("forma") or plan.get("forma"))
    if s.get("max_subida_m") is not None:
        m["pasa_tope"] = r["subida_m"] > s["max_subida_m"]
    return m


def correr(salidas, sesiones) -> list[dict]:
    filas = []
    for nombre, lat, lon in salidas:
        comuna = comuna_para(lat, lon)
        for texto, forma in sesiones:
            fila = {"salida": nombre, "comuna": comuna, "sesion": texto, "forma": forma}
            sesion = interpretar(texto)
            fila["tipo"] = sesion.tipo.value
            ctx = contexto(comuna)
            t0 = time.perf_counter()
            try:
                plan = planificar(ctx, sesion, lat, lon, forma=forma)
                fila["ok"] = True
                fila.update(medir(plan, texto))
            except SinTramo as exc:
                fila.update(ok=False, error=str(exc)[:160])
            fila["s"] = round(time.perf_counter() - t0, 2)
            filas.append(fila)
            print(f"  {nombre:28} {texto:18} {forma:9} {'ok ' if fila['ok'] else 'FALLA'} {fila['s']:5.1f} s", flush=True)
    return filas


# Qué métricas se resumen y si «más» es mejor (para marcar mejoras y retrocesos).
METRICAS = [
    ("s", "tiempo (s)", False), ("error_dist_abs", "error distancia (%)", False),
    ("semaforos_km", "semáforos/km", False), ("cruces_km", "cruces/km", False),
    ("repetido_pct", "repetido (%)", False), ("subida_km", "subida (m/km)", False),
    ("parque_pct", "parque (%)", True), ("semaforos_tramo", "semáforos en tramo", False),
    ("cruces_tramo", "cruces en tramo", False), ("acercamiento_m", "acercamiento (m)", False),
    ("semaforos_trote", "semáforos en trotes", False), ("error_pendiente", "error pendiente (pp)", False),
]


def resumen(filas: list[dict]) -> dict:
    out: dict = {}
    for tipo in sorted({f["tipo"] for f in filas}):
        grupo = [f for f in filas if f["tipo"] == tipo]
        r = {"n": len(grupo), "fallas": sum(not f["ok"] for f in grupo)}
        oks = [f | ({"error_dist_abs": abs(f["error_dist_pct"])} if "error_dist_pct" in f else {}) for f in grupo if f["ok"]]
        for clave, _, _ in METRICAS:
            vals = [f[clave] for f in oks if f.get(clave) is not None]
            if vals:
                r[clave] = round(statistics.mean(vals), 2)
        if any("pasa_tope" in f for f in oks):
            r["pasa_tope"] = sum(bool(f.get("pasa_tope")) for f in oks)
        out[tipo] = r
    return out


def tabla(res: dict, base: dict | None = None) -> str:
    lineas = []
    for tipo, r in res.items():
        b = (base or {}).get(tipo, {})
        lineas.append(f"\n### {tipo} · {r['n']} rutas · {r['fallas']} fallas"
                      + (f" (antes {b['fallas']})" if b else ""))
        lineas.append("| métrica | valor | antes | cambio |\n|---|---:|---:|---|")
        for clave, nombre, mas_mejor in METRICAS + [("pasa_tope", "se pasa del tope «plano»", False)]:
            if clave not in r:
                continue
            v, a = r[clave], b.get(clave)
            cambio = ""
            if a is not None and a != v:
                mejor = (v > a) == mas_mejor
                pct = f"{100 * (v - a) / a:+.0f} %" if a else ""
                cambio = ("✅ " if mejor else "⚠️ ") + pct
            lineas.append(f"| {nombre} | {v} | {'' if a is None else a} | {cambio} |")
    return "\n".join(lineas)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--etiqueta", default=datetime.now().strftime("%Y%m%d-%H%M"))
    ap.add_argument("--comparar", help="etiqueta de una corrida anterior")
    ap.add_argument("--rapido", action="store_true")
    a = ap.parse_args()
    salidas = [SALIDAS[i] for i in RAPIDO_SALIDAS] if a.rapido else SALIDAS
    sesiones = [SESIONES[i] for i in RAPIDO_SESIONES] if a.rapido else SESIONES
    t0 = time.time()
    filas = correr(salidas, sesiones)
    res = resumen(filas)
    carpeta = RAIZ / "banco" / "resultados"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / f"{a.etiqueta}.json").write_text(json.dumps(
        {"etiqueta": a.etiqueta, "fecha": datetime.now().isoformat(timespec="seconds"),
         "duracion_s": round(time.time() - t0), "resumen": res, "rutas": filas},
        ensure_ascii=False, indent=1), encoding="utf-8")
    base = None
    if a.comparar:
        base = json.loads((carpeta / f"{a.comparar}.json").read_text(encoding="utf-8"))["resumen"]
    texto = f"## Banco de rutas · {a.etiqueta}" + (f" vs {a.comparar}" if a.comparar else "") + "\n" + tabla(res, base)
    (carpeta / f"{a.etiqueta}.md").write_text(texto + "\n", encoding="utf-8")
    print(texto)


if __name__ == "__main__":
    main()
