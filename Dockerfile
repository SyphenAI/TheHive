# syntax=docker/dockerfile:1
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data STORAGE_DIR=/app/storage REGISTRY_PATH=/app/registry/providers.json
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt
COPY backend/app /app/backend/app
COPY registry /app/registry
COPY --from=ui /ui/dist /app/backend/static
RUN mkdir -p /app/data /app/storage && useradd --create-home --uid 10001 hive && chown -R hive:hive /app
USER hive
EXPOSE 50090
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD curl -fsS http://127.0.0.1:50090/api/health || exit 1
WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "50090"]
