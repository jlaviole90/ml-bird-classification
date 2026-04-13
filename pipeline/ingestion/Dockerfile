FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY ../../pyproject.toml .
RUN pip install --no-cache-dir \
    pyyaml confluent-kafka boto3 opencv-python-headless numpy

COPY pipeline/ingestion/ pipeline/ingestion/

CMD ["python", "pipeline/ingestion/extractor.py"]
