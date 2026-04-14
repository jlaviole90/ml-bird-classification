PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || command -v python3 || command -v python)

.PHONY: help up down build logs download-data train evaluate export test lint e2e e2e-web e2e-up e2e-down pi-up pi-down pi-logs

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Docker (local dev) ──────────────────────────────────

up: ## Start all local services
	docker compose up -d

down: ## Stop all local services
	docker compose down

build: ## Build all Docker images
	docker compose build

logs: ## Tail logs from all services
	docker compose logs -f

# ── Docker (Raspberry Pi) ───────────────────────────────

pi-up: ## Start Pi services
	docker compose -f docker-compose.pi.yml up -d --build

pi-down: ## Stop Pi services
	docker compose -f docker-compose.pi.yml down

pi-logs: ## Tail Pi service logs
	docker compose -f docker-compose.pi.yml logs -f

# ── Model ───────────────────────────────────────────────

download-data: ## Download CUB-200-2011 dataset
	$(PYTHON) model/data/download_cub200.py

train: ## Train the bird classifier
	$(PYTHON) model/train.py --config model/config/training_config.yaml

evaluate: ## Evaluate trained model
	$(PYTHON) model/evaluate.py --config model/config/training_config.yaml

export: ## Export model to TorchScript and archive .mar
	$(PYTHON) model/export_onnx.py --config model/config/training_config.yaml

# ── Quality ─────────────────────────────────────────────

test: ## Run the test suite
	pytest tests/ -v --cov

lint: ## Lint and format
	ruff check . --fix
	ruff format .

# ── E2E Testing ─────────────────────────────────────────

E2E_SERVICES := postgres torchserve catalog
E2E_IMAGE    ?= path/to/bird.jpg

e2e-up: ## Boot services needed for e2e (catalog, torchserve, postgres)
	docker compose up -d --build $(E2E_SERVICES)
	@echo "Waiting for services to be healthy..."
	@docker compose up -d --wait $(E2E_SERVICES) 2>/dev/null || \
		( until curl -sf http://localhost:8000/health > /dev/null 2>&1; do printf "."; sleep 2; done; echo " catalog ready"; \
		  until curl -sf http://localhost:8081/models > /dev/null 2>&1; do printf "."; sleep 2; done; echo " torchserve ready" )
	@echo "All services up."

e2e-down: ## Stop e2e services
	docker compose stop $(E2E_SERVICES)

e2e: e2e-up ## Full e2e test (no mocks): make e2e E2E_IMAGE=bird.jpg
	$(PYTHON) tests/integration/e2e_test.py image $(E2E_IMAGE) --live

e2e-web: e2e-up ## E2E web UI against live services
	$(PYTHON) tests/integration/e2e_test.py web --live
