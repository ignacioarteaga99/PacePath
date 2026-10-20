# PacePath — contexto para Claude

App para corredores: **ruta según el entrenamiento del día** (función
principal) + **«pinta tu ciudad»** (priorizar calles que el usuario no ha
corrido, a partir de su Strava). Ver `README.md` y `PLAN.md`.

## Contexto heredado (conversación donde nació el proyecto)
- Nace de **RaceEngine** (`ignacioarteaga99/RaceEngine`): motor de diseño de
  recorridos en Python (FastAPI) sobre grafos de OpenStreetMap con elevación,
  semáforos, cruces y superficie. Tiene versión móvil (`/m`) generada desde la
  de escritorio con `build_mobile.py`, desplegada en Render gratis
  (`raceengine.onrender.com`), carreras en Neon (Postgres) y grafos de comunas
  construidos en GitHub Actions y publicados en la Release `graphs`.
- **Lecciones de ese despliegue que aplican aquí:**
  - Render gratis: 512 MB de RAM, se duerme a los 15 min y **borra el disco**.
    Nada persistente en disco: datos en Postgres (Neon), grafos descargados.
  - Construir un grafo real necesita 0,6–1,6 GB → se hace en GitHub Actions,
    nunca en el servidor. Usarlo: 95–170 MB por comuna generando un 10K.
  - Mapas base: CARTO exige API key fuera de localhost; usar Esri u OSM.
  - Safari iOS: inputs a 16 px (si no, hace zoom), safe-area, mapa a pantalla
    completa con controles flotantes.
- El usuario es de Santiago de Chile y habla español: interfaz y textos en
  español (con inglés como segundo idioma si se agrega i18n).

## Cómo trabajar aquí
- Reutilizar RaceEngine como dependencia; los cambios al motor van a ese repo.
- Strava: OAuth propio de la app (cada usuario conecta su cuenta). Respetar los
  términos de la API (datos sólo para su dueño, borrar al desconectar).
- Tests con pytest; probar la interfaz en viewport de iPhone con Playwright
  (Chromium en `/opt/pw-browsers`).
