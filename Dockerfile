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

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.getenv('PORT', '8080'), timeout=4)"

# A single worker (with threads) so every request shares the same in-memory copy of the bucket
CMD exec gunicorn --bind ":${PORT:-8080}" --workers 1 --threads 8 --access-logfile - app:app
