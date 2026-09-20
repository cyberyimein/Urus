# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    APP_ENV=production \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    DATABASE_URL=sqlite:////data/urus.db \
    OBSERVATION_SCHEDULED_REQUIRE_SOURCE_CAPTURES=true \
    SIDECAR_CAPTURE_DATABASE_PATH=/data/urus-moomoo/collector.sqlite \
    MOOMOO_SDK_HOME=/data/moomoo_home \
    URUS_SCHEDULER_DATA_DIR=/data/scheduled_collection \
    MOOMOO_COLLECTION_LOCK_PATH=/data/moomoo_collection.lock \
    INTRADAY_OPTIONS_DATABASE_URL=sqlite:////data/intraday_options/intraday_options-v2.db \
    INTRADAY_OPTIONS_RAW_PAYLOAD_DIR=/data/intraday_options/raw \
    INTRADAY_OPTIONS_BACKUP_DIR=/backups/intraday_options

WORKDIR /app
RUN mkdir -p /data && pip install --no-cache-dir uv==0.8.4

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY backend/scripts/start_urus_api_container.py ./scripts/start_urus_api_container.py
COPY --from=frontend /src/frontend/dist ./frontend_dist

EXPOSE 8000

CMD ["uv", "run", "python", "scripts/start_urus_api_container.py"]
