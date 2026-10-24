# PacePath

**La ruta para tu entrenamiento de hoy, por calles reales.**

PacePath diseña la ruta ideal para la sesión que te toca —series, tempo, fondo
largo, cuestas, regenerativo— sobre las calles reales de tu ciudad. Si conectas
tu Strava, ves por dónde has corrido y la ruta prefiere tus calles de siempre y
tus tramos favoritos.

## Las dos funciones

### 1. Ruta según el entrenamiento del día (principal)
Eliges la sesión (o la lee de tu plan) y PacePath busca la ruta que la hace
posible, no sólo una que mida lo que pides:

| Sesión | Qué busca la ruta |
|---|---|
| Series (p. ej. 6×1 km) | Rectas planas de la longitud de la serie, sin semáforos ni cruces, cerca de casa; mejor en ida y vuelta o circuito |
| Tempo / umbral | Recorrido continuo, llano y con pocas detenciones |
| Fondo largo | Pocos cruces, fuentes de agua y baños en el camino, superficie amable |
| Cuestas | La subida de la pendiente y largo pedidos más cercana, con calentamiento y vuelta |
| Regenerativo | Corto, plano, con sombra y parques |

### 2. Mi ciudad y perfil (con Strava)
Con tu historial de Strava: un mapa de calor de por dónde has corrido (cuántas
veces por cada calle), los países y ciudades donde has corrido (al tocarlos, el
mapa te lleva ahí), tus calles de siempre y tramos favoritos como preferencia
al crear rutas, y un perfil con tus indicadores e historial (por año) y tu
**Pasaporte**: nivel, sellos de países y logros (leyenda local de tu comuna,
ciudades, continentes, rachas…).

## Requisito
El usuario conecta su cuenta de Strava (OAuth). Sin Strava la app genera rutas
por sesión igual, pero sin la capa personal.

## Base técnica
Reutiliza [RaceEngine](https://github.com/ignacioarteaga99/RaceEngine): su motor
de rutas sobre grafos de OpenStreetMap con elevación, semáforos y cruces, y los
grafos de comunas de Santiago ya publicados en su Release `graphs`.

Datos de terceros: calles, parques y nombres de [OpenStreetMap](https://www.openstreetmap.org/copyright)
(ODbL); ciudades de [GeoNames](https://www.geonames.org) (CC BY 4.0), en
`pacepath/datos/ciudades.json.gz` (se regenera con `scripts/ciudades.py`).

Plan de trabajo: [`PLAN.md`](PLAN.md).

## Desarrollo

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                          # PACEPATH_SIN_RED=1 salta los tests con grafo real
uvicorn pacepath.api:app --reload   # http://localhost:8000
```

Los grafos de comuna se descargan solos la primera vez desde la Release `graphs`
de RaceEngine a `data/` (variables `RACEENGINE_GRAPHS_URL`, `RACEENGINE_DATA`).
Los nombres de calle, desde la Release `nombres` de este repo
(`PACEPATH_NOMBRES_URL`; vacía = sin nombres).

```bash
curl -X POST localhost:8000/api/ruta -H 'Content-Type: application/json' \
     -d '{"lat": -33.4258, "lon": -70.61, "texto": "6x1k"}'
```

## Despliegue en Render (gratis)

`render.yaml` es un Blueprint: en render.com → **New → Blueprint** → este repo.
Imagen Docker (`Dockerfile`), salud en `/api/salud`, una comuna en memoria
(~200 MB de 512). El disco se borra al dormir: grafos y nombres se vuelven a
bajar solos de las Releases. La única variable secreta es `STRAVA_CLIENT_SECRET`
(Render la pide al crear el servicio).
