FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    opencv-python-headless \
    httpx \
    numpy \
    pyyaml

WORKDIR /app
COPY pipeline/worker/ pipeline/worker/

CMD ["python", "-m", "pipeline.worker.inference_worker"]
