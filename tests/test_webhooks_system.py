"""Tests for GET /admin/system."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from praxis.config import reset_settings
from praxis.main import app


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def admin_token(monkeypatch):
    token = "test-admin-token-1234567890abcdef1234567890"
    monkeypatch.setenv("PRAXIS_ADMIN_TOKEN", token)
    reset_settings()
    return token


def test_system_requires_auth(admin_token):
    """Without a bearer token, the endpoint returns 401."""
    client = TestClient(app)
    resp = client.get("/admin/system")
    assert resp.status_code == 401


def test_system_returns_health_snapshot(admin_token):
    """With a valid token, returns a snapshot containing the expected keys."""
    client = TestClient(app)
    resp = client.get(
        "/admin/system", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "router" in data
    assert "checkpointer" in data
    assert "services" in data


def test_system_router_snapshot_has_per_model_breakdowns(admin_token):
    """Important #5: the router snapshot must return per-model
    ``active``, ``peak``, and ``limits`` dicts so the TUI dashboard's
    router panel renders (it iterates ``router["active"].items()``).
    """
    client = TestClient(app)
    resp = client.get(
        "/admin/system", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    router = data["router"]
    # The dashboard reads these three keys.
    assert "active" in router, f"Missing 'active' key in router snapshot: {router}"
    assert "peak" in router, f"Missing 'peak' key in router snapshot: {router}"
    assert "limits" in router, f"Missing 'limits' key in router snapshot: {router}"
    # They must be dicts (per-model breakdowns), not scalars.
    assert isinstance(router["active"], dict)
    assert isinstance(router["peak"], dict)
    assert isinstance(router["limits"], dict)
    # Global totals are kept for backwards-compat.
    assert "active_global" in router
    assert "peak_global" in router


def test_system_snapshot_has_environment_and_tool_protocol(admin_token):
    """The snapshot also reports environment and tool_protocol."""
    client = TestClient(app)
    resp = client.get(
        "/admin/system", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "environment" in data
    assert "tool_protocol" in data
