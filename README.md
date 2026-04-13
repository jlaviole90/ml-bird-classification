# Bird Species Classification Pipeline

A media metadata enrichment pipeline that ingests live video from a bird feeder camera, extracts frames, classifies bird species using a fine-tuned EfficientNet-B4 model, and stores enriched metadata in a searchable catalog.

## Architecture

| Component | Technology |
|-----------|------------|
| Model training | PyTorch, EfficientNet-B4, CUB-200-2011 |
| Model serving | TorchServe |
| Stream ingestion | ffmpeg, OpenCV, Kafka |
| Real-time processing | Apache Flink (PyFlink) |
| Batch processing | Apache Spark (PySpark) |
| Metadata catalog | PostgreSQL + TimescaleDB, Elasticsearch |
| API | FastAPI |
| Object storage | MinIO (local) / AWS S3 |
| Observability | Prometheus, Grafana |
| Infrastructure | Docker Compose (local), Terraform + AWS |

## Quick Start

```bash
# Clone and enter the repo
git clone https://github.com/jlaviole90/ml-bird-classification.git
cd ml-bird-classification

# Copy environment config
cp .env.example .env

# Start infrastructure services
make up

# Download CUB-200-2011 dataset and train the model
make download-data
make train

# Export model for serving
make export

# Run tests
make test
```

## Project Layout

```
model/          PyTorch training code (dataset, model, training loop)
serving/        TorchServe handler and Dockerfile
pipeline/
  ingestion/    HLS frame extractor → Kafka
  flink/        Real-time inference pipeline
  spark/        Batch analytics and training data prep
catalog/        FastAPI metadata catalog + Elasticsearch sync
dashboard/      Grafana dashboards and Prometheus config
infra/          Terraform modules for AWS deployment
tests/          Unit, integration, and end-to-end tests
```

## Development

```bash
# Install Python dependencies
pip install -e ".[dev]"

# Lint
make lint

# Test
make test
```
