FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates templates

RUN useradd --create-home app
USER app

EXPOSE 8080

CMD exec gunicorn --bind ":${PORT:-8080}" --workers 2 --threads 4 --access-logfile - app:app
