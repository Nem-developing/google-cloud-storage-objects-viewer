FROM ghcr.io/astral-sh/uv:0.13.3 AS uv

FROM python:3.15-slim AS builder

COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /app/.venv .venv
COPY pyproject.toml app.py ./
COPY templates templates

RUN useradd --create-home app
USER app

EXPOSE 8080

CMD exec gunicorn --bind ":${PORT:-8080}" --workers 2 --threads 4 --access-logfile - app:app
