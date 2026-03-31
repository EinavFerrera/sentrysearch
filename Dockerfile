# Optimus Vision (sentrysearch web) — Gemini embedding + ChromaDB + ffmpeg
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SENTRYSEARCH_DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY sentrysearch ./sentrysearch

RUN uv sync --frozen --no-dev --extra web

ENV PATH="/app/.venv/bin:$PATH"

RUN mkdir -p /data/db /data/uploads /data/clips

EXPOSE 7778

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:7778/api/health || exit 1

CMD ["sentrysearch", "serve", "--host", "0.0.0.0", "--port", "7778"]
