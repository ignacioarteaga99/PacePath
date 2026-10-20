"""La sesión de entrenamiento y lo que le exige a una ruta.

Dos piezas:

  · `Sesion`: lo que el corredor tiene que hacer hoy («6×1000 m, pausa 90 s»).
  · `Requisitos`: lo que eso le pide a la calle. Es la traducción de la sesión a
    restricciones duras y pesos blandos; el generador de cada tipo sólo lee esto.

Separarlas permite discutir los números de cada tipo de sesión en un solo sitio
y probarlos sin grafo.
"""
from __future__ import annotations

import dataclasses
import re
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

    @model_validator(mode="after")
    def _completa(self) -> "Sesion":
        if self.tipo in (TipoSesion.SERIES, TipoSesion.CUESTAS):
            if not self.repeticiones or not self.distancia_rep_m:
                raise ValueError("las series y cuestas necesitan repeticiones y distancia")
            if self.pausa_s is None:
                self.pausa_s = 90 if self.tipo is TipoSesion.SERIES else 0
            if self.tipo is TipoSesion.CUESTAS and self.pendiente_pct is None:
                self.pendiente_pct = 6.0
        elif not self.distancia_m:
            raise ValueError(f"una sesión de {self.tipo.value} necesita distancia")
        return self

    @property
    def resumen(self) -> str:
        t = self.tipo
        if t is TipoSesion.SERIES:
            return (f"{self.repeticiones}×{_m(self.distancia_rep_m)}"
                    f" (pausa {self.pausa_s} s)")
        if t is TipoSesion.CUESTAS:
            return (f"cuestas {self.repeticiones}×{_m(self.distancia_rep_m)}"
                    f" al {self.pendiente_pct:g} %")
        tope = f" (máx. +{self.max_subida_m:.0f} m)" if self.max_subida_m is not None else ""
        return f"{t.value} {_m(self.distancia_m)}{tope}"


def _m(metros: float | None) -> str:
    if metros is None:
        return "?"
    if metros >= 1000 and metros % 100 == 0:
        return f"{metros / 1000:g} km".replace(".", ",")
    return f"{metros:.0f} m"


# ── Escritura rápida: «6x1k», «8x400 r60», «fondo 18k», «cuestas 8x300 6%» ──

_DIST = r"(\d+(?:[.,]\d+)?)\s*(k|km|m)?"
_TIPOS = {
    "series": TipoSesion.SERIES, "serie": TipoSesion.SERIES,
    "intervalos": TipoSesion.SERIES,
    "tempo": TipoSesion.TEMPO, "umbral": TipoSesion.TEMPO,
    "fondo": TipoSesion.FONDO, "largo": TipoSesion.FONDO, "tirada": TipoSesion.FONDO,
    "cuestas": TipoSesion.CUESTAS, "cuesta": TipoSesion.CUESTAS,
    "regenerativo": TipoSesion.REGENERATIVO, "suave": TipoSesion.REGENERATIVO,
    "rodaje": TipoSesion.REGENERATIVO,
}


def _metros(num: str, unidad: str | None) -> float:
    v = float(num.replace(",", "."))
    if unidad in ("k", "km"):
        return v * 1000
    if unidad == "m":
        return v
    # Sin unidad: «6x1» son kilómetros, «8x400» son metros.
    return v * 1000 if v < 50 else v


_TOPE = re.compile(
    r"(?:d\s*\+\s*|\+\s*|desnivel\s*(?:m[aá]x(?:imo)?\s*)?(?:de\s*)?|"
    r"subida\s*(?:m[aá]x(?:ima)?\s*)?(?:de\s*)?|m[aá]x(?:imo)?\s*\+?\s*)(\d+)\s*m?\b")


def interpretar(texto: str) -> Sesion:
    """Convierte lo que el corredor escribe en una `Sesion`.

    >>> interpretar("6x1k").resumen
    '6×1 km (pausa 90 s)'
    """
    t = texto.strip().lower().replace("×", "x")
    tipo = next((v for k, v in _TIPOS.items() if re.search(rf"\b{k}\b", t)), None)

    reps = re.search(rf"(\d+)\s*x\s*{_DIST}", t)
    if reps:
        n = int(reps.group(1))
        d = _metros(reps.group(2), reps.group(3))
        pausa = re.search(r"\b(?:r|p|pausa|rec)\s*(\d+)\s*(s|seg|min|')?", t)
        pausa_s = None
        if pausa:
            pausa_s = int(pausa.group(1)) * (60 if pausa.group(2) in ("min", "'") else 1)
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
    dist = re.search(rf"\b{_DIST}\b", t)
    if tipo is None or tipo in (TipoSesion.SERIES, TipoSesion.CUESTAS) or not dist:
        raise ValueError(f"no entiendo la sesión «{texto}». Prueba «6x1k», "
                         "«tempo 8k», «fondo 18k», «cuestas 8x300 6%» o «regenerativo 6k»")
    return Sesion(tipo=tipo, distancia_m=_metros(dist.group(1), dist.group(2) or "k"),
                  max_subida_m=float(tope.group(1)) if tope else None)


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
    notas: tuple[str, ...] = field(default_factory=tuple)


def requisitos(s: Sesion) -> Requisitos:
    """Traduce la sesión a lo que se busca en la calle."""
    req = _requisitos_base(s)
    if s.max_subida_m is None or s.tipo in (TipoSesion.SERIES, TipoSesion.CUESTAS):
        return req
    # Con un tope de desnivel la subida se encarece al buscar, más cuanto más
    # estricto sea el tope por km (2 m/km → 40 m extra por metro subido).
    km = (s.distancia_m + (s.calentamiento_m + s.vuelta_calma_m
                           if s.tipo is TipoSesion.TEMPO else 0)) / 1000
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
        mult_sin_pavimento=0.9, peso_parque=60.0,
        peso_detencion=15.0, peso_subida=3.0, subida_tolerada_m_km=6.0,
        notas=("Regenerativo: plano y por parques. No hay datos de sombra; los "
               "parques son la mejor aproximación.",),
    )
