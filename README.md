# PacePath

**Tu entrenamiento de hoy, por calles que nunca has corrido.**

PacePath diseña la ruta ideal para la sesión que te toca —series, tempo, fondo
largo, cuestas, regenerativo— sobre las calles reales de tu ciudad. Conectas tu
Strava y, además, la ruta prioriza las calles que todavía no has pisado: cada
entrenamiento pinta un poco más tu mapa.

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

### 2. Pinta tu ciudad
Con tu historial de Strava, PacePath sabe qué calles ya corriste: porcentaje
recorrido por comuna, mapa de cobertura y la opción **«priorizar calles
nuevas»** en cualquier ruta generada.

## Requisito
El usuario conecta su cuenta de Strava (OAuth). Sin Strava la app genera rutas
por sesión igual, pero sin la capa de calles nuevas.

## Base técnica
Reutiliza [RaceEngine](https://github.com/ignacioarteaga99/RaceEngine): su motor
de rutas sobre grafos de OpenStreetMap con elevación, semáforos y cruces, y los
grafos de comunas de Santiago ya publicados en su Release `graphs`.

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
