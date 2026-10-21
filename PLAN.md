# Plan de PacePath

Prioridad del producto: **la ruta según el entrenamiento del día es la función
principal**. Con Strava se suma la capa personal: por dónde has corrido (mapa
de calor), tus calles de siempre y tramos favoritos, y un perfil con tu
historial. **Decidido (Ignacio): no hay «lo que te falta por correr»** — ni
cobertura por comuna ni priorizar calles nuevas; sólo por dónde has corrido.

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
- [x] Generadores de tempo, fondo, cuestas y regenerativo.
- [x] Volver o no a la salida en **todas** las sesiones: con meta, la vuelta a
      la calma (o el fondo) termina ahí; sin meta, donde se cumple lo pedido.
- [x] Tope de desnivel (tempo, fondo, regenerativo): número («+100m») o
      «plano» / «flat» = 5 m de subida por km de la sesión.
- [x] Radio máximo (cualquier sesión): nada de la ruta sale del círculo
      alrededor de la salida (calles permitidas en los pesos, tramos de series
      con ambos extremos dentro, corredores recortados); se dibuja en el mapa.
- [x] Forma de la ruta (volviendo a la salida): auto (lo mejor; a menudo ida y
      vuelta), **circuito** (sólo bucles del motor, repetir calle pesa ×5–15;
      fondo 12k en Providencia: de 100 % repetido a 5 %) o **vueltas ×N** a un
      circuito más corto (en tempo, bloque y calentamiento en vueltas enteras).
- [x] Series y cuestas aisladas: la sesión son las repeticiones. El trote para
      llegar se dibuja (punteado) y se dice su distancia; no hay vuelta a la
      calma ni en el mapa, ni en el texto, ni en el GPX; la altimetría es sólo
      el bloque. «Volver», forma y meta no aplican y se ocultan.
- [x] Opciones plegables en el teléfono, con un resumen de lo elegido.
- [x] Altimetría con cada intervalo marcado (banda, líneas y número).
- [x] Recientes (10) y sesiones guardadas (30) en el teléfono: se guardan los
      parámetros y, como el motor es determinista, repetir da la misma ruta.
- [x] Buscador de la salida: calles, esquinas, parques y **direcciones con
      número** («Pocuro 2500», vía Nominatim con caché y ≤ 1 petición/s).

## Fase 2 — Strava
- [x] OAuth de Strava (app propia, scopes `read,activity:read_all`). Sin base
      de datos por ahora: tokens en cookie cifrada (Fernet, clave derivada del
      secreto), se renuevan solos; desconectar revoca el acceso en Strava.
- [x] «Mi ciudad»: mapa de calor con tus carreras (polilínea resumida de cada
      actividad), guardadas en el navegador (IndexedDB) e importación
      incremental (`after=`).
- [ ] Importar actividades de carrera (detalle con `map.polyline`; streams
      `latlng` cuando haga falta precisión). Respetar límites de la API
      (reintentos con espera; importación incremental por fecha).
- [ ] Webhook de Strava para importar actividades nuevas solas.
- [ ] Segmentos de Strava (`segments/explore`, tipo running) con el token del
      usuario, al armar SU ruta: bonificar tramos que son segmentos populares
      (señal de que se corren y tienen buena vereda) y usar los de subida como
      candidatos de cuestas. Sólo 10 por consulta y sin capa global compartida
      (términos de la API): complementan a OSM, no lo reemplazan.
- [ ] Cumplir los términos de la API de Strava: los datos de cada usuario sólo
      para ese usuario, borrado al desconectar, sin usarlos para entrenar modelos.

## Fase 3 — Por dónde has corrido
- [x] Calles de siempre: tus carreras → aristas (KD-tree sobre las aristas,
      trazado densificado cada 8 m, 20 m de tolerancia). El teléfono las pide
      una vez por comuna y las adjunta a cada ruta; pesan en pesos, corredores
      y puntuación de tempo, fondo y regenerativo.
- [x] Tramos favoritos: se trazan a mano y se guardan en el teléfono; pesan
      más que las calles de siempre.
- [x] Mapa de calor con conteo real de veces por calle y escala.
- [x] Perfil: indicadores de Strava (4 semanas, año, histórico) e historial.
- [x] Perfil por año: totales, comparación con el año anterior (el año en curso,
      a la misma fecha), semanas del año, ritmo por mes, récords y hábitos.
- [x] Dónde has corrido: países y ciudades (salidas agrupadas a 20 km en el
      teléfono; nombre = la ciudad más importante cerca, con GeoNames en el
      servidor, sin servicios externos). Tocar uno lleva el mapa ahí.
- ~~Cobertura por comuna, calles pendientes y priorizar calles nuevas~~:
  descartado por decisión de producto.

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
