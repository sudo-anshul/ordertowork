# syntax=docker/dockerfile:1
ARG UV_VERSION=0.11.7
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM node:22-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS backend
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./backend/
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_SYNC=1 \
    UV_NO_DEV=1 \
    UV_CACHE_DIR=/tmp/uv-cache \
    OTW_DATA_DIR=/var/lib/ordertowork \
    OTW_FRONTEND_DIST=/app/frontend/dist
WORKDIR /app
RUN useradd --no-log-init --create-home --uid 10001 otw \
    && mkdir -p /var/lib/ordertowork \
    && chown 10001:10001 /var/lib/ordertowork
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=backend /app/.venv/ /app/.venv/
COPY --from=frontend /app/frontend/dist/ /app/frontend/dist/
COPY pyproject.toml uv.lock alembic.ini ./
COPY migrations/ ./migrations/
COPY deploy/rds-global-bundle.pem /etc/ssl/certs/rds-global-bundle.pem
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "ordertowork.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
