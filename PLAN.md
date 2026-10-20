# Plan de PacePath

Prioridad del producto: **la ruta según el entrenamiento del día es la función
principal**; «calles nuevas» es una capa que la enriquece (y una vista propia de
cobertura). Ambas requieren que el usuario conecte su Strava para la parte
personal.

## Arquitectura

```
iPhone (web móvil, instalable)  ──►  API PacePath (FastAPI)
                                       ├─ motor de RaceEngine (dependencia)
                                       ├─ grafos de comunas (Release «graphs» de RaceEngine)
                                       ├─ Strava OAuth + importación de actividades
                                       └─ Postgres (Neon): usuarios, tokens, cobertura, rutas
```

- **RaceEngine como dependencia, no copiado.** Instalar sus paquetes
  (`raceengine_contracts`, `raceengine_routing`, `raceengine_geodata`,
  `raceengine_engine`) desde el repo por git (`pip install
  "git+https://github.com/ignacioarteaga99/RaceEngine#subdirectory=packages/engine"`,
  etc.) o como submódulo. Si hace falta cambiar el motor, se cambia en RaceEngine.
- **Grafos:** `RACEENGINE_GRAPHS_URL=https://github.com/ignacioarteaga99/RaceEngine/releases/download/graphs`
  (manifest.json + `<id>.pkl.gz`). Disponibles: santiago_centro, las_condes,
  providencia, nunoa, vitacura, lo_barnechea. Grafo = CSR (indptr, dst) con
  atributos por arista (elen, eclass, egrad, epaved, elit, epark, eclosure…) y
  por nodo (lat, lon, elev, is_signal).
- **Despliegue:** mismo esquema que RaceEngine (Render gratis + Neon), móvil primero.

## Fase 0 — Esqueleto
- [x] Estructura del repo, FastAPI, dependencias de RaceEngine, tests con pytest.
- [x] Cargar un grafo de comuna desde la Release y generar una ruta con el motor.
- [x] Interfaz móvil mínima (mapa a pantalla completa, controles flotantes,
      inputs a 16 px). Leaflet vendorizado en `web/vendor/`.
- [x] Nombres de calle: el grafo no los trae. Workflow «Nombres de calle»
      (Overpass en CI) → Release `nombres` de PacePath; se cruzan con el grafo
      por id de nodo OSM. Trae también fuentes de agua y baños (para fondo).
      **Propuesta para RaceEngine:** guardar un `ename` por arista en el grafo.

## Fase 1 — Ruta según la sesión (función principal)
- [x] Modelo de sesión: `tipo` (series, tempo, fondo, cuestas, regenerativo),
      distancia o duración, y parámetros (p. ej. 6×1000 m con 90 s de pausa;
      cuestas 8×300 m al 6 %).
- [ ] Traducir cada sesión a pesos y restricciones del motor:
  - [x] Series: buscar **tramos rectos y planos** de la longitud de la repetición
    sin semáforos (`is_signal`) ni cruces mayores; armar calentamiento → bloque
    de series → vuelta. Métrica nueva: «tramo apto para series».
  - Tempo: penalizar semáforos, cruces y giros cerrados; pendiente baja.
  - Fondo largo: pocos cruces, preferir parques (`epark`), puntos de agua
    (OSM `amenity=drinking_water`, `toilets`; hay que añadirlos al grafo).
  - Cuestas: localizar segmentos con pendiente/largo pedidos (`egrad`, `elen`)
    cerca de la salida.
  - Regenerativo: plano, parques, corto.
- [x] Explicación de la ruta (series) («Las series van por Av. Pocuro: 1,1 km recto,
      0 semáforos, desnivel 4 m»).
- [x] Exportar GPX con marcas de cada repetición (series).
- [ ] Generadores de tempo, fondo, cuestas y regenerativo (hoy la API responde 501).

## Fase 2 — Strava
- [ ] OAuth de Strava (app propia en strava.com/settings/api; scopes
      `read,activity:read_all`). Guardar tokens cifrados; refrescar.
- [ ] Importar actividades de carrera (detalle con `map.polyline`; streams
      `latlng` cuando haga falta precisión). Respetar límites de la API
      (reintentos con espera; importación incremental por fecha).
- [ ] Webhook de Strava para importar actividades nuevas solas.
- [ ] Cumplir los términos de la API de Strava: los datos de cada usuario sólo
      para ese usuario, borrado al desconectar, sin usarlos para entrenar modelos.

## Fase 3 — Calles nuevas
- [ ] **Map-matching:** proyectar cada traza GPS al grafo y marcar aristas
      recorridas (tolerancia ~20–25 m, exigir continuidad para no marcar
      calles paralelas por ruido GPS).
- [ ] Cobertura por usuario: aristas recorridas (bitset por región) y % por
      comuna (contando metros de calle, no aristas).
- [ ] Peso «calle nueva» en el generador: bonificar metros no recorridos, con
      un control de intensidad (0 = ignorar, 1 = máximo).
- [ ] Mapa de cobertura (recorridas vs. pendientes) y logros por comuna.

## Fase 4 — Producto
- [ ] Leer la sesión del día desde el plan del usuario (o escribirla rápido:
      «6x1k», «fondo 18k», «cuestas 8x300»).
- [ ] Despliegue en Render + Neon; PWA instalable en iPhone.

## Cómo funciona «series» (implementado)
1. `tramos.py` reconstruye «trazos» rectos del grafo (strokes: en cada nodo se
   empareja la continuación más recta, desvío < 35°). Cada nodo del trazo lleva
   semáforo (o semáforo a < 25 m de un cruce) y coste de cruce según la clase
   de las otras vías (senda 0, residencial 1, colectora 2, avenida 3).
2. `series.py` evalúa todas las ventanas del largo de la repetición. Duro: 0
   semáforos, 0 cruces de colectora/avenida, desnivel ≤ máx(6 m, 1,2 %), recta.
   Blando: cruces menores, desnivel, curvatura, distancia extra más allá del
   calentamiento, parque. Si nada cumple se relaja por niveles y se avisa.
3. Calentamiento y vuelta con los pesos del motor de RaceEngine (`build_weights`,
   llano y con pocos semáforos) + sendas de parque; repeticiones en ida y vuelta.

## Decisiones abiertas
- Cuánto vale ir más lejos frente a cruzar calles: desde Plaza Ñuñoa el mejor
  1 km sin cruces está a 4,4 km (Andrés Bello); el más cercano tiene 10–16
  cruces residenciales. Hoy gana el lejano.
- Fuente de puntos de agua/baños (OSM) → ampliar el grafo en RaceEngine.
- Cómo combinar comunas en una ruta (RaceEngine ya soporta regiones compuestas
  `a+b`).
