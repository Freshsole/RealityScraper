# One image, two roles: docker-compose.yml runs it as `web` and as `worker`.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd --uid 10001 --create-home app \
    && mkdir -p /data \
    && chown -R app:app /data /app
USER app

VOLUME /data
EXPOSE 8000

# Default = web. The worker service overrides `command` in docker-compose.yml.
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --timeout-graceful-shutdown 20"]
