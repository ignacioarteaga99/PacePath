FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY vendor/ vendor/
COPY pacepath/ pacepath/
COPY web/ web/
# El motor (RaceEngine, repositorio privado) viene como wheel en vendor/.
RUN pip install --no-cache-dir vendor/raceengine-*.whl -e .

# Grafos y nombres se descargan aquí al usarse (el disco de Render gratis se
# borra al dormir; da igual, se vuelven a bajar en segundos).
ENV RACEENGINE_DATA=/app/data
# Menos «arenas» de memoria por hilo: con el threadpool de FastAPI y los
# cambios de comuna, la memoria quedaba fragmentada (360 MB → 270 MB).
ENV MALLOC_ARENA_MAX=2
ENV PORT=8000
EXPOSE 8000

# PORT lo fija Render (10000); en local queda en 8000.
CMD ["sh", "-c", "exec python -m uvicorn pacepath.api:app --host 0.0.0.0 --port ${PORT}"]
