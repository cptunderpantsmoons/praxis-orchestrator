"""Verify existing admin endpoints now require auth."""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from praxis.config import reset_settings
from praxis.main import app


@pytest.fixture(autouse=True)
def _reset_settings():
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def admin_token(monkeypatch):
    token = "test-admin-token-1234567890abcdef1234567890"
    monkeypatch.setenv("PRAXIS_ADMIN_TOKEN", token)
    reset_settings()
    return token


def test_failed_events_requires_token(admin_token):
    client = TestClient(app)
    resp = client.get("/admin/failed-events")
    assert resp.status_code == 401


def test_failed_events_with_token_passes_auth(admin_token, monkeypatch):
    # The endpoint may fail for other reasons (no failed-events file), but
    # it must NOT fail with 401 when a valid token is provided.
    monkeypatch.setattr(
        "praxis.webhooks.admin._failed_events_path",
        lambda _settings: pathlib.Path("/nonexistent/path.jsonl"),
    )
    client = TestClient(app)
    resp = client.get(
        "/admin/failed-events",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code != 401
    assert resp.status_code != 403


def test_retry_failed_requires_token(admin_token):
    client = TestClient(app)
    resp = client.post("/admin/retry-failed", json={"event_id": "x"})
    assert resp.status_code == 401
