"""La sesión de entrenamiento y lo que le exige a una ruta.

Dos piezas:

  · `Sesion`: lo que el corredor tiene que hacer hoy («6×1000 m»; la pausa,
    sólo si la escribe: «6x1k r60»).
  · `Requisitos`: lo que eso le pide a la calle. Es la traducción de la sesión a
    restricciones duras y pesos blandos; el generador de cada tipo sólo lee esto.

Separarlas permite discutir los números de cada tipo de sesión en un solo sitio
y probarlos sin grafo.
"""
from __future__ import annotations

import dataclasses
import math
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class TipoSesion(str, Enum):
    SERIES = "series"
    TEMPO = "tempo"
    FONDO = "fondo"
    CUESTAS = "cuestas"
    REGENERATIVO = "regenerativo"


class Sesion(BaseModel):
    tipo: TipoSesion
    # Series y cuestas
    repeticiones: int | None = Field(default=None, ge=1, le=40)
    distancia_rep_m: float | None = Field(default=None, ge=100, le=5000)
    pausa_s: int | None = Field(default=None, ge=0, le=900)
    pendiente_pct: float | None = Field(default=None, ge=1, le=20)
    # Tempo, fondo, regenerativo: distancia del bloque principal
    distancia_m: float | None = Field(default=None, ge=1000, le=60_000)
    # Tempo, fondo, regenerativo: límite de desnivel positivo de toda la sesión
    max_subida_m: float | None = Field(default=None, ge=0, le=5000)
    # Calentamiento y vuelta a la calma (trote suave hasta y desde el tramo)
    calentamiento_m: float = Field(default=2000, ge=0, le=10_000)
    vuelta_calma_m: float = Field(default=1500, ge=0, le=10_000)
    # Pirámide o escalera: el largo de cada repetición («200-400-600-400-200»).
    # Se corren sobre un tramo del largo mayor.
    escalera_m: list[float] | None = Field(default=None, min_length=2, max_length=20)
    # Por tiempo: «45 min suave», «fondo 1h30», fartlek «10x1'». La distancia
    # sale del ritmo habitual del corredor (de sus carreras de Strava) o, sin
    # él, de 6:00/km.
    duracion_s: int | None = Field(default=None, ge=300, le=6 * 3600)
    rep_s: int | None = Field(default=None, ge=10, le=1800)
    ritmo_s_km: float | None = Field(default=None, ge=150, le=720)

    @model_validator(mode="after")
    def _completa(self) -> "Sesion":
        if self.escalera_m:
            self.repeticiones = len(self.escalera_m)
            self.distancia_rep_m = max(self.escalera_m)
        ritmo = self.ritmo_s_km or RITMO_POR_DEFECTO
        if self.rep_s and not self.distancia_rep_m:
            self.distancia_rep_m = _redondear(self.rep_s / (ritmo * RITMO_FARTLEK) * 1000, 10, 100)
        if self.duracion_s and not self.distancia_m and self.tipo not in (TipoSesion.SERIES, TipoSesion.CUESTAS):
            self.distancia_m = _redondear(self.duracion_s / (ritmo * RITMO_TIPO[self.tipo]) * 1000, 100, 1000)
        if self.tipo in (TipoSesion.SERIES, TipoSesion.CUESTAS):
            if not self.repeticiones or not self.distancia_rep_m:
                raise ValueError("las series y cuestas necesitan repeticiones y distancia")
            if self.tipo is TipoSesion.CUESTAS and self.pendiente_pct is None:
                self.pendiente_pct = 6.0
        elif not self.distancia_m:
            raise ValueError(f"una sesión de {self.tipo.value} necesita distancia")
        return self

    @property
    def resumen(self) -> str:
        from .idioma import tr
        t = self.tipo
        if t is TipoSesion.SERIES and self.escalera_m:
            km = all(x >= 1000 and x % 100 == 0 for x in self.escalera_m)
            pasos = "-".join(f"{x / 1000:g}".replace(".", ",") if km else f"{x:.0f}" for x in self.escalera_m)
            return tr(f"pirámide {pasos} {'km' if km else 'm'}", f"pyramid {pasos} {'km' if km else 'm'}",
                                                                 f"pirâmide {pasos} {'km' if km else 'm'}")
        if t is TipoSesion.SERIES and self.rep_s:
            pausa = (tr(f" / {_tiempo(self.pausa_s)} suave", f" / {_tiempo(self.pausa_s)} easy",
                                                             f" / {_tiempo(self.pausa_s)} leve")
                     if self.pausa_s else "")
            return (f"fartlek {self.repeticiones}×{_tiempo(self.rep_s)}{pausa}"
                    f" (≈ {_m(self.distancia_rep_m)})")
        if t is TipoSesion.SERIES:
            # La pausa sólo si el corredor la escribió: no se inventa.
            pausa = (tr(f" (pausa {self.pausa_s} s)", f" ({self.pausa_s} s rest)",
                                                      f" ({self.pausa_s} s de descanso)")
                     if self.pausa_s else "")
            return f"{self.repeticiones}×{_m(self.distancia_rep_m)}{pausa}"
        if t is TipoSesion.CUESTAS:
            return tr(f"cuestas {self.repeticiones}×{_m(self.distancia_rep_m)}"
                      f" al {self.pendiente_pct:g} %",
                      f"hills {self.repeticiones}×{_m(self.distancia_rep_m)}"
                      f" at {self.pendiente_pct:g} %",
                      f"subidas {self.repeticiones}×{_m(self.distancia_rep_m)}"
                      f" a {self.pendiente_pct:g} %")
        tope = tr(f" (máx. +{self.max_subida_m:.0f} m)", f" (max +{self.max_subida_m:.0f} m)",
                                                         f" (máx. +{self.max_subida_m:.0f} m)") \
            if self.max_subida_m is not None else ""
        nombre = {TipoSesion.TEMPO: ("tempo", "tempo", "tempo"), TipoSesion.FONDO: ("fondo", "long run", "longão"),
                  TipoSesion.REGENERATIVO: ("regenerativo", "easy run", "regenerativo")}[t]
        if self.duracion_s:
            return f"{tr(*nombre)} {_tiempo(self.duracion_s)} (≈ {_m(self.distancia_m)}){tope}"
        return f"{tr(*nombre)} {_m(self.distancia_m)}{tope}"


# Ritmo por defecto (sin Strava) y cuánto más rápido o lento se corre cada
# tipo respecto del ritmo habitual del corredor.
RITMO_POR_DEFECTO = 360.0                 # 6:00 min/km
RITMO_TIPO = {TipoSesion.REGENERATIVO: 1.06, TipoSesion.FONDO: 1.0, TipoSesion.TEMPO: 0.86}
RITMO_FARTLEK = 0.82                      # los cambios de ritmo, cerca del de 5 km


def _redondear(metros: float, paso: float, minimo: float) -> float:
    return max(minimo, round(metros / paso) * paso)


def _tiempo(s: int | None) -> str:
    if not s:
        return "?"
    h, m = divmod(round(s / 60), 60) if s >= 60 else (0, 0)
    if s < 60:
        return f"{s}″"
    if h:
        return f"{h} h {m} min" if m else f"{h} h"
    return f"{m}′" if s % 60 == 0 and m <= 10 else f"{m} min"


def _m(metros: float | None) -> str:
    if metros is None:
        return "?"
    if metros >= 1000 and metros % 100 == 0:
        return f"{metros / 1000:g} km".replace(".", ",")
    if metros >= 5000:                          # p. ej. 10 millas: «16,09 km», no «16093 m»
        return f"{metros / 1000:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " km"
    return f"{metros:.0f} m"


# ── Escritura rápida: «6x1k», «8x400 r60», «fondo 18k», «cuestas 8x300 6%» ──

# Millas («10 mi», «6x1 mile», «10 millas»): para quien corre en Estados Unidos o el Reino Unido.
_DIST = r"(\d+(?:[.,]\d+)?)\s*(mi(?:llas?|les?|lhas?)?(?![a-z])|k|km|m)?"
MILLA_M = 1609.344
_TIPOS = {
    "series": TipoSesion.SERIES, "serie": TipoSesion.SERIES,
    "intervalos": TipoSesion.SERIES,
    "tempo": TipoSesion.TEMPO, "umbral": TipoSesion.TEMPO,
    "fondo": TipoSesion.FONDO, "largo": TipoSesion.FONDO, "tirada": TipoSesion.FONDO,
    "cuestas": TipoSesion.CUESTAS, "cuesta": TipoSesion.CUESTAS,
    "regenerativo": TipoSesion.REGENERATIVO, "suave": TipoSesion.REGENERATIVO,
    "rodaje": TipoSesion.REGENERATIVO, "trote": TipoSesion.REGENERATIVO,
    "fartlek": TipoSesion.TEMPO,
    # English
    "intervals": TipoSesion.SERIES, "interval": TipoSesion.SERIES, "reps": TipoSesion.SERIES,
    "threshold": TipoSesion.TEMPO, "long": TipoSesion.FONDO,
    "hills": TipoSesion.CUESTAS, "hill": TipoSesion.CUESTAS,
    "easy": TipoSesion.REGENERATIVO, "recovery": TipoSesion.REGENERATIVO,
    # Português
    "tiros": TipoSesion.SERIES, "intervalado": TipoSesion.SERIES, "intervalados": TipoSesion.SERIES,
    "limiar": TipoSesion.TEMPO, "longão": TipoSesion.FONDO, "longao": TipoSesion.FONDO,
    "longo": TipoSesion.FONDO, "subidas": TipoSesion.CUESTAS,
    "leve": TipoSesion.REGENERATIVO, "rodagem": TipoSesion.REGENERATIVO,
}


def _metros(num: str, unidad: str | None) -> float:
    v = float(num.replace(",", "."))
    if unidad in ("k", "km"):
        return v * 1000
    if unidad and unidad.startswith("mi"):
        return v * MILLA_M
    if unidad == "m":
        return v
    # Sin unidad: «6x1» son kilómetros, «8x400» son metros.
    return v * 1000 if v < 50 else v


_TOPE = re.compile(
    r"(?:d\s*\+\s*|\+\s*|desnivel\s*(?:m[aá]x(?:imo)?\s*)?(?:de\s*)?|"
    r"subida\s*(?:m[aá]x(?:ima)?\s*)?(?:de\s*)?|m[aá]x(?:imo)?\s*\+?\s*)(\d+)\s*m?\b")


# «plano», «llano», «flat»: tope de desnivel de 5 m por km de la sesión (un 18k
# plano admite +90 m). Santiago sube hacia la cordillera: sin tope, un fondo
# hacia el oriente suma fácil 10–15 m/km.
_PLANO = re.compile(r"\b(?:plano|llano|flat)\b")
PLANO_M_POR_KM = 5.0


# Pirámide o escalera: «pirámide 200-400-600-400-200», «piramide 1-2-3-2-1 km», «pirâmide…».
_PIRAMIDE = re.compile(r"\b(?:pir[aáâ]mide|pyramid|escalera|escada|ladder)\b")
_PASOS = re.compile(r"(\d+(?:[.,]\d+)?)(?:\s*(?:-|–|,\s|/|\s)\s*(?=\d))?")
# Repeticiones por tiempo (fartlek): «10x1'», «8x3 min r2'», «6x30s».
_REPS_TIEMPO = re.compile(r"(\d+)\s*x\s*(\d+(?:[.,]\d+)?)\s*(min(?:utos?|s)?\b|'|’|s\b|seg\b|\"|″)")
# Duración de una sesión continua: «45 min», «45'», «1h30», «1 h 15 min», «1 hora».
_DURACION = re.compile(r"(?:(\d+)\s*h(?:oras?|rs?)?(?![a-z])\s*(?:(\d+)\s*(?:min(?:utos?|s)?\b|'|’)?)?"
                       r"|(\d+)\s*(?:min(?:utos?|s)?\b|'|’))")


def _segundos(num: str, unidad: str) -> int:
    v = float(num.replace(",", "."))
    return round(v * 60 if unidad.startswith("min") or unidad in ("'", "’") else v)


def _pausa(t: str) -> int | None:
    pausa = re.search(r"\b(?:r|p|pausa|rec)\s*(\d+)\s*(s|seg|min|'|’)?", t) \
        or re.search(r"/\s*(\d+)\s*(s|seg|min|'|’)", t)
    if not pausa:
        return None
    return int(pausa.group(1)) * (60 if pausa.group(2) in ("min", "'", "’") else 1)


def interpretar(texto: str, ritmo_s_km: float | None = None) -> Sesion:
    """Convierte lo que el corredor escribe en una `Sesion`. `ritmo_s_km`: su
    ritmo habitual (para las sesiones por tiempo).

    >>> interpretar("6x1k").resumen
    '6×1 km'
    """
    t = texto.strip().lower().replace("×", "x")
    tipo = next((v for k, v in _TIPOS.items() if re.search(rf"\b{k}\b", t)), None)
    ritmo = ritmo_s_km if ritmo_s_km and 150 <= ritmo_s_km <= 720 else None

    if _PIRAMIDE.search(t):
        resto = t[_PIRAMIDE.search(t).end():]
        pausa_s = _pausa(resto)
        resto = re.sub(r"\b(?:r|p|pausa|rec)\s*\d+\s*(?:s|seg|min|'|’)?|/\s*\d+\s*(?:s|seg|min|'|’)", " ", resto)
        pasos = [m.group(1) for m in _PASOS.finditer(resto)]
        unidad = re.search(r"\d\s*(k|km|m)\b", resto)
        if len(pasos) >= 2:
            u = unidad.group(1) if unidad else None
            return Sesion(tipo=TipoSesion.SERIES, escalera_m=[_metros(p, u) for p in pasos[:20]],
                          pausa_s=pausa_s)

    rt = _REPS_TIEMPO.search(t)
    if rt:
        return Sesion(tipo=TipoSesion.SERIES, repeticiones=int(rt.group(1)),
                      rep_s=_segundos(rt.group(2), rt.group(3)), ritmo_s_km=ritmo,
                      pausa_s=_pausa(t[rt.end():]))

    reps = re.search(rf"(\d+)\s*x\s*{_DIST}", t)
    if reps:
        n = int(reps.group(1))
        d = _metros(reps.group(2), reps.group(3))
        pausa_s = _pausa(t)
        pend = re.search(r"(\d+(?:[.,]\d+)?)\s*%", t)
        return Sesion(
            tipo=tipo if tipo is TipoSesion.CUESTAS else TipoSesion.SERIES,
            repeticiones=n, distancia_rep_m=d, pausa_s=pausa_s,
            pendiente_pct=float(pend.group(1).replace(",", ".")) if pend else None,
        )

    # Límite de desnivel: «+100m», «d+100», «desnivel 100», «subida máx 100 m».
    tope = _TOPE.search(t)
    if tope:
        t = t[:tope.start()] + " " + t[tope.end():]
    dur = _DURACION.search(t)
    duracion_s = None
    if dur:
        h, m_h, m = dur.groups()
        duracion_s = int(h) * 3600 + int(m_h or 0) * 60 if h else int(m) * 60
        t = t[:dur.start()] + " " + t[dur.end():]
    dist = re.search(rf"\b{_DIST}\b", t)
    if tipo is None and duracion_s:
        tipo = TipoSesion.REGENERATIVO          # «45 min» a secas: trote suave
    if duracion_s and not dist and tipo not in (None, TipoSesion.SERIES, TipoSesion.CUESTAS):
        s = Sesion(tipo=tipo, duracion_s=duracion_s, ritmo_s_km=ritmo,
                   max_subida_m=float(tope.group(1)) if tope else None)
        if s.max_subida_m is None and _PLANO.search(t):
            s.max_subida_m = float(math.ceil(PLANO_M_POR_KM * _km_sesion(s) / 10) * 10)
        return s
    if tipo is None or tipo in (TipoSesion.SERIES, TipoSesion.CUESTAS) or not dist:
        from .idioma import tr
        raise ValueError(tr(f"No entiendo la sesión «{texto}». Prueba «6x1k», «tempo 8k», "
                            "«fondo 18k», «45 min suave», «cuestas 8x300 6%», "
                            "«pirámide 200-400-600-400-200» o «fartlek 10x1'».",
                            f"I don't understand «{texto}». Try «6x1k», «tempo 8k», "
                            "«long run 18k», «easy 45 min», «hills 8x300 6%», "
                            "«pyramid 200-400-600-400-200» or «fartlek 10x1'».",
                            f"Não entendi «{texto}». Tente «6x1k», «tempo 8k», "
                            "«longão 18k», «leve 45 min», «subidas 8x300 6%», "
                            "«pirâmide 200-400-600-400-200» ou «fartlek 10x1'»."))
    s = Sesion(tipo=tipo, distancia_m=_metros(dist.group(1), dist.group(2) or "k"),
               max_subida_m=float(tope.group(1)) if tope else None)
    if s.max_subida_m is None and _PLANO.search(t):
        # Un número explícito («+60m») manda sobre «plano».
        s.max_subida_m = float(math.ceil(PLANO_M_POR_KM * _km_sesion(s) / 10) * 10)
    return s


def _km_sesion(s: Sesion) -> float:
    """Km sobre los que se mide el tope: en tempo, con calentamiento y vuelta a la calma."""
    return (s.distancia_m + (s.calentamiento_m + s.vuelta_calma_m
                             if s.tipo is TipoSesion.TEMPO else 0)) / 1000


# ── Traducción a requisitos de la calle ─────────────────────────────────────

# Coste de cruzar una vía según su clase (OSM `highway`). Sendas, veredas y
# ciclovías no cortan una serie: valen 0. Una avenida sin semáforo es peor que
# una calle residencial: hay que frenar y mirar.
COSTE_CRUCE = {
    "primary": 3.0, "primary_link": 3.0, "trunk": 3.0, "trunk_link": 3.0,
    "secondary": 3.0, "secondary_link": 3.0,
    "tertiary": 2.0, "tertiary_link": 2.0,
    "residential": 1.0, "unclassified": 1.0, "living_street": 0.6,
    "service": 0.3,
}
CRUCE_MAYOR = 2.0       # colectora o avenida
# Desde aquí cuenta como cruce de calle (y Ignacio no acepta ninguno dentro de
# una repetición). Por debajo quedan las entradas de servicio —estacionamientos,
# accesos a edificios— que sólo restan puntos.
CRUCE_MENOR = 0.6


@dataclass(frozen=True)
class Requisitos:
    """Lo que una sesión le pide a la calle.

    Los campos `max_*` son restricciones duras (un tramo que las viola no se
    ofrece, salvo como último recurso y avisando). Los `peso_*` son blandos:
    puntos de penalización para ordenar los tramos que sí cumplen.

    Hay dos familias de sesión:
      · De TRAMO (series, cuestas, y el corredor de un tempo): se busca un tramo
        de `largo_tramo_m` y se repite. Usan los campos de tramo.
      · De CIRCUITO (fondo, regenerativo, y el circuito de un tempo): se genera
        un circuito de `distancia_m` con el motor de RaceEngine. Usan los
        multiplicadores `mult_*`, que se aplican al coste de cada arista.
    """

    tipo: TipoSesion
    # ── Tramo ──
    largo_tramo_m: float = 0.0
    max_semaforos: int = 0
    max_cruces_mayores: int = 0
    max_cruces_menores: int = 0
    max_desnivel_tramo_m: float = 12.0      # máx − mín dentro de la repetición
    min_rectitud: float = 0.85              # cuerda / largo
    pendiente_objetivo_pct: float | None = None   # cuestas: subida media buscada
    tolerancia_pendiente_pct: float = 1.5
    peso_cruce: float = 25.0                # por cruce de servicio (× coste)
    peso_desnivel_m: float = 4.0            # por metro de máx − mín
    peso_pendiente: float = 30.0            # por punto de % lejos del objetivo
    peso_curvatura: float = 100.0           # × (1 − rectitud)
    peso_lejania_100m: float = 4.0          # por cada 100 m de acercamiento extra
    bonus_parque: float = 10.0
    peso_sin_pavimento: float = 5.0
    sendas_de_parque: bool = True
    # ── Circuito ──
    distancia_m: float = 0.0
    # Detenciones como metros EXTRA, no como multiplicador: las aristas del grafo
    # miden ~10 m, así que «×1,35 al entrar a un semáforo» costaba 3 m y el
    # circuito pasaba por cincuenta semáforos sin inmutarse.
    penal_semaforo_m: float = 0.0           # metros extra al entrar a un semáforo
    penal_cruce_m: float = 0.0              # metros extra × coste del cruce
    # Subir también como metros extra: regla de Naismith, 1 m de desnivel
    # positivo ≈ 8 m en plano. Con multiplicadores, el descuento de parque
    # dejaba barato cruzar el San Cristóbal (566 m de subida en un fondo).
    penal_subida_m_por_m: float = 0.0
    mult_parque: float = 1.0
    mult_subida: float = 0.0                # coste × (1 + esto × % de subida)
    mult_sin_pavimento: float = 1.0
    radio_agua_m: float = 0.0               # fondo: premiar pasar junto a fuentes
    max_subida_m: float | None = None       # duro: desnivel positivo máximo
    # Para elegir entre circuitos candidatos (puntos por km):
    peso_detencion: float = 40.0            # por detención probable / km
    peso_subida: float = 0.5                # por m de subida / km
    # Desnivel «normal» para la sesión (m de subida por km). Lo que pase de aquí
    # se castiga fuerte: un fondo sin semáforos que sube al San Cristóbal no es
    # un fondo. Santiago oriente tiene ~1 % de pendiente general: un ida y vuelta
    # hacia la cordillera ya suma ~10 m/km sin que haya ningún cerro.
    subida_tolerada_m_km: float = 1e9
    peso_exceso_subida: float = 8.0         # por m/km por encima de lo tolerado
    peso_parque: float = 10.0               # puntos menos por ir 100 % por parque
    # Preferencias del corredor: float32[E] (1 = neutro, <1 = preferida) con sus
    # calles de siempre (Strava) y sus tramos favoritos. Lo arma la API por
    # petición; no se guarda.
    preferidas: object = None
    peso_preferida: float = 40.0            # puntos menos por ir 100 % por ellas
    # Según cuándo se corre (ver momento.py): senderos de parque y caminos sin
    # luz de noche, y avenidas en hora punta.
    mult_sin_luz: float = 1.0
    mult_avenida: float = 1.0
    notas: tuple[str, ...] = field(default_factory=tuple)


# Cuándo se corre (momento.Momento): lo fija planificar() para cada pedido.
MOMENTO: ContextVar = ContextVar("momento", default=None)


def requisitos(s: Sesion) -> Requisitos:
    """Traduce la sesión a lo que se busca en la calle (y a cuándo se corre)."""
    return _por_momento(_requisitos_tope(s), MOMENTO.get())


def _por_momento(req: Requisitos, m) -> Requisitos:
    if m is None or m.neutro:
        return req
    cambios: dict = {}
    if m.oscuro:
        # Senderos de parque y caminos de tierra sin luz: mucho más caros. El
        # parque deja de sumar puntos (de noche, el que no tiene luz resta).
        cambios.update(mult_sin_luz=3.0, peso_parque=0.0, bonus_parque=0.0,
                       mult_parque=max(req.mult_parque, 1.0))
    elif m.calor:
        # Sin datos de sombra: los parques son lo más parecido.
        cambios.update(mult_parque=req.mult_parque * 0.85, peso_parque=req.peso_parque * 1.5,
                       bonus_parque=req.bonus_parque * 1.5)
    if m.punta:
        cambios.update(penal_semaforo_m=req.penal_semaforo_m * 1.3, penal_cruce_m=req.penal_cruce_m * 1.5,
                       mult_avenida=1.2, peso_detencion=req.peso_detencion * 1.2)
    return dataclasses.replace(req, **cambios)


def _requisitos_tope(s: Sesion) -> Requisitos:
    req = _requisitos_base(s)
    if s.max_subida_m is None or s.tipo in (TipoSesion.SERIES, TipoSesion.CUESTAS):
        return req
    # Con un tope de desnivel la subida se encarece al buscar, más cuanto más
    # estricto sea el tope por km (2 m/km → 40 m extra por metro subido).
    km = _km_sesion(s)
    por_km = s.max_subida_m / max(km, 0.1)
    return dataclasses.replace(
        req, max_subida_m=float(s.max_subida_m),
        penal_subida_m_por_m=max(req.penal_subida_m_por_m, min(40.0, 80.0 / max(por_km, 1.0))),
        subida_tolerada_m_km=min(req.subida_tolerada_m_km, por_km))


def _requisitos_base(s: Sesion) -> Requisitos:
    t = s.tipo
    if t is TipoSesion.SERIES:
        largo = float(s.distancia_rep_m)
        return Requisitos(
            tipo=t,
            largo_tramo_m=largo,
            # Plano: en 1 km, más de ~1 % de desnivel ya cambia el ritmo. Para
            # repeticiones cortas se tolera algo más en proporción (el GPS y el
            # modelo de elevación SRTM tienen ±2–3 m de ruido).
            max_desnivel_tramo_m=max(6.0, 0.012 * largo),
            # Una recta perfecta de 2 km no existe en casi ninguna comuna: se
            # pide menos rectitud cuanto más larga es la repetición.
            min_rectitud=0.90 if largo <= 600 else 0.85 if largo <= 1200 else 0.75,
            notas=("Las repeticiones van en ida y vuelta por el mismo tramo: "
                   "la pendiente que quede se compensa.",),
        )
    if t is TipoSesion.CUESTAS:
        return Requisitos(
            tipo=t,
            largo_tramo_m=float(s.distancia_rep_m),
            pendiente_objetivo_pct=float(s.pendiente_pct),
            # Una cuesta puede curvar; lo que no puede es tener cruces ni
            # semáforos a mitad de subida.
            min_rectitud=0.55,
            max_desnivel_tramo_m=1e9,
            peso_desnivel_m=0.0,
            peso_curvatura=20.0,
            notas=("Cada repetición es de subida; la bajada es la recuperación, "
                   "trotando suave.",),
        )
    if t is TipoSesion.TEMPO:
        return Requisitos(
            tipo=t,
            distancia_m=float(s.distancia_m),
            # Para el corredor de ida y vuelta: el tramo más largo posible sin
            # detenciones; `largo_tramo_m` lo fija el generador.
            max_desnivel_tramo_m=15.0,
            min_rectitud=0.6,
            peso_curvatura=10.0,
            # Para el circuito: cada detención rompe el ritmo umbral.
            penal_semaforo_m=400.0, penal_cruce_m=120.0, mult_subida=0.06,
            mult_parque=0.9, peso_detencion=60.0,
            # Ritmo umbral: mejor pavimento parejo que tierra o maicillo.
            mult_sin_pavimento=1.15,
            notas=("El bloque de tempo va por donde hay menos detenciones: "
                   "calienta y suelta fuera de él.",),
        )
    if t is TipoSesion.FONDO:
        return Requisitos(
            tipo=t,
            distancia_m=float(s.distancia_m),
            penal_semaforo_m=200.0, penal_cruce_m=50.0, mult_parque=0.75,
            # Subida cara al buscar: el Cerro San Cristóbal es parque, y con el
            # descuento de parque un camino por el cerro salía «barato».
            mult_subida=0.0, penal_subida_m_por_m=8.0,
            mult_sin_pavimento=0.95, peso_parque=60.0,
            radio_agua_m=60.0, peso_detencion=40.0, peso_subida=1.5,
            subida_tolerada_m_km=12.0, peso_exceso_subida=15.0,
            notas=("Fondo largo: pocos cruces, parque cuando se puede y fuentes "
                   "de agua en el camino (las que figuran en OpenStreetMap).",),
        )
    # Regenerativo
    return Requisitos(
        tipo=t,
        distancia_m=float(s.distancia_m),
        penal_semaforo_m=100.0, penal_cruce_m=25.0, mult_parque=0.60,
        mult_subida=0.0, penal_subida_m_por_m=15.0,
        # Superficie blanda (tierra, maicillo) cuando la hay: cuida las piernas.
        mult_sin_pavimento=0.8, peso_parque=60.0,
        peso_detencion=15.0, peso_subida=3.0, subida_tolerada_m_km=6.0,
        notas=("Regenerativo: plano y por parques. No hay datos de sombra; los "
               "parques son la mejor aproximación.",),
    )
