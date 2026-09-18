# ── build ───────────────────────────────────────────────────────────────
# Imagen slim, no alpine: py-clob-client-v2 arrastra dependencias de
# criptografía (coincurve, eth-hash) que en alpine hay que compilar a mano.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias del sistema para las ruedas que sí compilan
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Capa de dependencias aparte: el código cambia mucho más que requirements,
# así que esto se cachea entre deploys y los builds bajan de minutos a segundos.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Usuario sin privilegios. Si alguien logra ejecución remota, que al menos
# no sea root.
RUN useradd --create-home --uid 10001 bot && chown -R bot:bot /app
USER bot

# El healthcheck de Render pega aquí; también lo usa UptimeRobot para
# evitar el spin-down de los 15 minutos.
EXPOSE 10000

CMD ["python", "-m", "pilot_a_llm.run"]
