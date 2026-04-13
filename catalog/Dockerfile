FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi[standard] \
    uvicorn[standard] \
    sqlalchemy[asyncio] \
    asyncpg \
    psycopg2-binary \
    alembic \
    elasticsearch \
    confluent-kafka \
    boto3 \
    prometheus-client \
    pydantic-settings \
    httpx \
    apscheduler \
    python-json-logger

COPY catalog/ catalog/

EXPOSE 8000
CMD ["uvicorn", "catalog.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
