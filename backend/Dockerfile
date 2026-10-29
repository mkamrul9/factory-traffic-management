FROM ghcr.io/astral-sh/uv:0.11.16 AS uv
FROM python:3.14-slim

COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    FACTORY_TRAFFIC_DB=/data/factory-traffic.sqlite3

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable && mkdir -p /data

EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn factory_traffic.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 1 --no-access-log"]
