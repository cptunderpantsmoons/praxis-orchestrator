"""Tests for /admin/metrics* endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from praxis.config import reset_settings
from praxis.features.metrics import get_metrics
from praxis.main import app


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    get_metrics().reset()
    yield
    reset_settings()
    get_metrics().reset()


@pytest.fixture
def admin_token(monkeypatch):
    token = "test-admin-token-1234567890abcdef1234567890"
    monkeypatch.setenv("PRAXIS_ADMIN_TOKEN", token)
    reset_settings()
    return token


def test_metrics_endpoint_requires_auth(admin_token):
    client = TestClient(app)
    resp = client.get("/admin/metrics")
    assert resp.status_code == 401


def test_metrics_endpoint_returns_snapshot(admin_token):
    registry = get_metrics()
    registry.inc_counter("model_calls:umans-flash", 5)
    client = TestClient(app)
    resp = client.get(
        "/admin/metrics", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["counters"]["model_calls:umans-flash"] == 5


def test_metrics_prom_endpoint_requires_auth(admin_token):
    client = TestClient(app)
    resp = client.get("/admin/metrics/prom")
    assert resp.status_code == 401


def test_metrics_prom_endpoint_returns_text(admin_token):
    registry = get_metrics()
    registry.inc_counter("model_calls:umans-flash", 2)
    client = TestClient(app)
    resp = client.get(
        "/admin/metrics/prom", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "model_calls" in resp.text


def test_metrics_reset_requires_auth(admin_token):
    client = TestClient(app)
    resp = client.post("/admin/metrics/reset")
    assert resp.status_code == 401


def test_metrics_reset_zeros_all(admin_token):
    registry = get_metrics()
    registry.inc_counter("foo", 5)
    registry.set_gauge("bar", 3)
    registry.observe_histogram("baz", 42)
    client = TestClient(app)
    resp = client.post(
        "/admin/metrics/reset", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    snap = registry.snapshot()
    assert snap["counters"] == {}
    assert snap["gauges"] == {}
    assert snap["histograms"] == {}
