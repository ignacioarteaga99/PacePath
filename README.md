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
