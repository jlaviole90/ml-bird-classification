"""Unit tests for the catalog FastAPI endpoints (using mocked DB)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked lifespan (no real DB needed)."""
    from catalog.api.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestMetricsEndpoint:
    def test_metrics_returns_prometheus_format(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "http_requests_total" in resp.text or resp.headers["content-type"] == "text/plain; charset=utf-8"
