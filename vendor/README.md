# vendor/

`raceengine-2.0.0-py3-none-any.whl`: el motor de rutas, construido desde
RaceEngine en el commit **a271967** («Camino mínimo con el Dijkstra de scipy»).

RaceEngine es privado, así que Render (y los workflows de este repo) no pueden
instalarlo desde GitHub: se instala desde este archivo (ver el `Dockerfile`).

Para actualizar el motor, desde una copia de RaceEngine en el commit deseado:

    pip wheel --no-deps /ruta/a/RaceEngine -w vendor/

y cambiar el commit en este archivo. Instalar en local:

    pip install vendor/raceengine-*.whl -e ".[dev]"
