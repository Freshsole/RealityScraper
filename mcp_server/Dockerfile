FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    CATALOG_BASE_URL=https://realitify.cz \
    PUBLIC_WEB_URL=https://realitify.cz

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY catalog_client.py server.py facts.yaml ./

EXPOSE 8000

CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
