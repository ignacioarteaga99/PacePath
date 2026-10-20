FROM python:3.12-slim

# git: RaceEngine se instala desde su repositorio (dependencia por git).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY pacepath/ pacepath/
COPY web/ web/
RUN pip install --no-cache-dir -e .

# Grafos y nombres se descargan aquí al usarse (el disco de Render gratis se
# borra al dormir; da igual, se vuelven a bajar en segundos).
ENV RACEENGINE_DATA=/app/data
ENV PORT=8000
EXPOSE 8000

# PORT lo fija Render (10000); en local queda en 8000.
CMD ["sh", "-c", "exec python -m uvicorn pacepath.api:app --host 0.0.0.0 --port ${PORT}"]
