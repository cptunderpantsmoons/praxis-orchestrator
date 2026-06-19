"""Quality Gate 1: Application boots and passes the health check."""

import time

from fastapi.testclient import TestClient


def test_health_check_returns_ok(client: TestClient):
    """GET /health returns 200 with status 'ok' and correct version."""
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert data["version"] != ""


def test_health_check_has_fast_response(client: TestClient):
    """Health check responds quickly (sub-200ms target)."""
    start = time.monotonic()
    response = client.get("/health")
    elapsed_ms = (time.monotonic() - start) * 1000

    assert response.status_code == 200
    assert elapsed_ms < 200, f"Health check took {elapsed_ms:.1f}ms (target: <200ms)"
