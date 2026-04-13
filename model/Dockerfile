FROM pytorch/pytorch:2.2.1-cuda12.1-cudnn8-runtime

WORKDIR /app
COPY ../pyproject.toml .
RUN pip install --no-cache-dir ".[dev]"

COPY model/ model/
COPY serving/handler.py serving/handler.py

CMD ["python", "model/train.py", "--config", "model/config/training_config.yaml"]
