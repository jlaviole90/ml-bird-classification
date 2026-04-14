# Bird Species Classification Pipeline

An end-to-end ML pipeline that watches a live bird feeder camera, detects birds in frame using YOLOv8, classifies species with a fine-tuned EfficientNet-B4 model, validates identifications against the eBird database, and stores results in a searchable catalog. Runs on a Raspberry Pi 5 alongside a live HLS stream served to [jlav.io](https://jlav.io).

## Architecture

```
Reolink RLC-811A (PoE, 4K)
    |
    |--- RTSP (main stream) ---> FFmpeg (systemd) ---> HLS ---> Tailscale Funnel ---> jlav.io/birds
    |
    |--- RTSP (sub stream) ----> Inference Worker (Docker)
                                      |
                                      |-- 1. Motion detection (OpenCV contour analysis)
                                      |-- 2. Bird pre-filter (YOLOv8-nano, COCO "bird" class)
                                      |-- 3. Crop to bird bounding box
                                      |-- 4. Species classification (TorchServe, EfficientNet-B4)
                                      |-- 5. eBird validation (Catalog API, Bayesian re-weighting)
                                      |-- 6. Store detection + update yard life list (PostgreSQL)
```

### Pi 5 Deployment (4 containers)

| Container | Role | Port (host) |
|-----------|------|-------------|
| **postgres** | TimescaleDB -- detections, eBird data, yard life list | 5430 |
| **torchserve** | Serves the EfficientNet-B4 `.mar` model | 8082 |
| **catalog** | FastAPI -- eBird validation, detection storage, REST API | 8000 |
| **worker** | Inference loop -- RTSP frames, YOLO, TorchServe, Catalog | -- |

### Full Development Stack (additional services)

The `docker-compose.yml` includes the full distributed architecture for local development and future AWS deployment: Kafka, Flink, Spark, MinIO, Elasticsearch, Prometheus, and Grafana.

## Inference Pipeline

Each frame goes through a multi-stage filtering process before a detection is recorded:

1. **Frame extraction** -- FFmpeg pulls the RTSP sub-stream at 1 fps (3 fps on motion)
2. **Motion detection** -- OpenCV frame differencing with contour analysis rejects noise (wind, lighting changes) and requires a bird-sized blob
3. **Cooldown** -- minimum 5 seconds between inference requests to avoid spamming
4. **YOLOv8-nano bird pre-filter** -- confirms a bird is actually in the frame (COCO class 14). Non-bird motion (squirrels, people, wind) is discarded
5. **Crop and classify** -- the bird's bounding box is cropped with 15% padding and sent to TorchServe running EfficientNet-B4 (200 CUB-200 species)
6. **eBird validation** -- predictions are re-weighted using eBird seasonal frequency data for the configured region. Impossible species are rejected and the next candidate is promoted
7. **Storage** -- validated detections are persisted to PostgreSQL with a full audit trail. The yard life list is updated automatically

## eBird Integration

The Catalog API integrates with [eBird API 2.0](https://documenter.getpostman.com/view/664302/S1ENwy59) to validate ML predictions against real-world ornithological data:

- **Local species list** -- maintained and synced daily; species not on the list are flagged
- **Seasonal frequency** -- weekly frequency data is used as a Bayesian prior to re-weight ML confidence scores
- **Rerouting** -- if the top prediction is impossible for the region/season, it's rejected and the highest-ranking valid candidate is promoted
- **Notable sightings** -- rare species are flagged for manual review
- **Audit log** -- every identification decision is logged with the full reasoning chain (candidates considered, rejection reasons, frequency data used)

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for training and local development)
- An eBird API key ([request here](https://ebird.org/api/keygen))

### Train and Export the Model

```bash
# Create virtual environment
python3 -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Copy environment config
cp .env.example .env
# Edit .env with your eBird API key and camera credentials

# Download CUB-200-2011 dataset (~1.2GB)
make download-data

# Train the bird classifier
make train

# Export to TorchServe .mar archive
make export
```

### Deploy on Raspberry Pi 5

See [streaming/README.md](streaming/README.md) for full setup instructions including camera configuration, HLS streaming, and Docker deployment.

```bash
# On the Pi, after cloning and configuring .env:
docker compose -f docker-compose.pi.yml up -d --build

# Watch detections in real time
docker compose -f docker-compose.pi.yml logs -f worker

# Check the yard life list
curl http://localhost:8000/api/v1/yard-list | python3 -m json.tool
```

### Run Tests

```bash
make test                # Unit tests with coverage
make e2e-web             # E2E web UI against live services
make e2e E2E_IMAGE=bird.jpg  # Single image E2E test
```

## API Endpoints

The Catalog API (port 8000) exposes these endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Service health check |
| `/api/v1/validate` | POST | Validate predictions against eBird and store detection |
| `/api/v1/yard-list` | GET | Cumulative yard life list |
| `/api/v1/yard-list/stats` | GET | Yard list summary (total species, coverage) |
| `/api/v1/ebird/local-species` | GET | Local species list with current-week frequency |
| `/api/v1/ebird/notable` | GET | Recent notable (rare) sightings |
| `/api/v1/ebird/hotspots` | GET | Nearby birding hotspots |
| `/api/v1/detections/{id}/audit` | GET | Full audit trail for a detection |
| `/api/v1/audit/rerouted` | GET | Detections where eBird overrode the model |
| `/api/v1/audit/stats` | GET | Aggregate audit metrics (reroute rate, rejection rate) |
| `/api/v1/species/{id}/migration` | GET | Detection timeline overlaid with eBird seasonal frequency |

## Project Layout

```
model/                    PyTorch training, evaluation, and export
  src/                    Dataset, model architecture, transforms, utilities
  config/                 Training hyperparameters (training_config.yaml)
  train.py                Training loop with MPS/CUDA/CPU support
  evaluate.py             Model evaluation
  export_onnx.py          Export to TorchScript + .mar archive

serving/                  TorchServe model serving
  handler.py              Custom handler (preprocess, inference, postprocess)
  config.properties       TorchServe configuration
  model_store/            .mar archive (gitignored, ~67MB)
  Dockerfile              Multi-arch (ARM64 + AMD64) TorchServe image

catalog/                  FastAPI metadata catalog
  api/
    main.py               App entrypoint, lifespan, eBird sync scheduler
    db.py                 Async SQLAlchemy engine
    routes/               REST endpoints (detections, eBird, search, species)
    ebird/                eBird client, validator, sync service, audit logging
    models/               SQLAlchemy ORM + Pydantic schemas
  migrations/             Alembic database migrations
  Dockerfile              Catalog API image

pipeline/
  worker/                 Inference worker for Pi deployment
    inference_worker.py   RTSP -> motion -> YOLO -> TorchServe -> Catalog loop
    config.yaml           Worker configuration (all values env-overridable)
    Dockerfile            Worker image (Python + FFmpeg + OpenCV + YOLO)
  ingestion/              Frame extractor (Kafka-based, for full stack)
  flink/                  PyFlink real-time inference job (for full stack)
  spark/                  PySpark batch analytics (for full stack)

streaming/                Raspberry Pi streaming setup
  start_stream.sh         FFmpeg RTSP-to-HLS transcoder with auto-reconnect
  nginx-hls.conf          Nginx config for serving HLS segments
  birdcam-stream.service  systemd unit file
  README.md               Full Pi setup guide

dashboard/                Observability and sightings UI
  prometheus.yml          Prometheus scrape config
  provisioning/           Grafana dashboards and datasources
  sightings/              Angular sightings dashboard components for jlav.io

tests/
  unit/                   Unit tests (pytest) for all modules
  integration/            E2E testing tool (CLI + web UI)

infra/                    AWS infrastructure (Terraform)
  terraform/              ECS, Kinesis, Lambda, S3, SageMaker modules

docker-compose.yml        Full local development stack (14 services)
docker-compose.pi.yml     Lightweight Pi deployment (4 services)
Makefile                  Common development tasks
```

## Configuration

All worker configuration lives in `pipeline/worker/config.yaml` with environment variable overrides:

| Setting | Default | Description |
|---------|---------|-------------|
| `stream.base_fps` | 1.0 | Frame rate when idle |
| `stream.motion_fps` | 3.0 | Frame rate when motion detected |
| `motion.cooldown_seconds` | 5.0 | Minimum seconds between inference requests |
| `motion.min_contour_area` | 500 | Minimum pixel area for a motion blob |
| `bird_detector.confidence` | 0.4 | YOLOv8 confidence threshold for "bird" |
| `bird_detector.padding_fraction` | 0.15 | Padding around bird crop (fraction of box size) |
| `inference.min_confidence` | 0.10 | Minimum species confidence to store a detection |

## Makefile Targets

```
make help            Show all targets
make up              Start full local stack (docker-compose.yml)
make down            Stop all services
make train           Train the bird classifier
make export          Export model to .mar archive
make test            Run unit tests with coverage
make lint            Lint and format with ruff
make e2e-web         Launch E2E web UI against live services
```

## Tech Stack

| Category | Technologies |
|----------|-------------|
| ML / Training | PyTorch, EfficientNet-B4, CUB-200-2011 |
| Bird Detection | YOLOv8-nano (COCO pre-trained) |
| Model Serving | TorchServe |
| Species Validation | eBird API 2.0, Bayesian re-weighting |
| API | FastAPI, SQLAlchemy (async), Pydantic |
| Database | PostgreSQL + TimescaleDB |
| Streaming | FFmpeg, Nginx, HLS, Tailscale Funnel |
| Camera | Reolink RLC-811A (PoE, 4K, RTSP) |
| Deployment | Docker Compose, Raspberry Pi 5 |
| CI/CD | GitHub Actions |
| Infrastructure | Terraform (AWS: ECS, S3, Kinesis, SageMaker) |
| Observability | Prometheus, Grafana |
| Testing | pytest, E2E web UI tool |
