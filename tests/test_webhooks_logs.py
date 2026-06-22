"""Tests for /admin/logs/* endpoints."""
from __future__ import annotations

import json

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


def _write_audit_log(path, entries):
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


def test_audit_logs_requires_auth(admin_token):
    client = TestClient(app)
    resp = client.get("/admin/logs/audit")
    assert resp.status_code == 401


def test_audit_logs_returns_entries(admin_token, tmp_path, monkeypatch):
    log_path = tmp_path / "audit.jsonl"
    _write_audit_log(
        log_path,
        [
            {"event": "webhook.graph_invoke", "level": "info", "ts": "2026-06-22T10:00:00Z"},
            {"event": "webhook.graph_failed", "level": "error", "ts": "2026-06-22T11:00:00Z"},
        ],
    )
    monkeypatch.setattr(
        "praxis.webhooks.logs._audit_log_path", lambda: log_path
    )

    client = TestClient(app)
    resp = client.get(
        "/admin/logs/audit", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 2


def test_audit_logs_missing_file_returns_empty(admin_token, tmp_path, monkeypatch):
    """If the audit log file does not exist, return an empty entries list."""
    missing = tmp_path / "does-not-exist.jsonl"
    monkeypatch.setattr(
        "praxis.webhooks.logs._audit_log_path", lambda: missing
    )
    client = TestClient(app)
    resp = client.get(
        "/admin/logs/audit", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["entries"] == []


def test_audit_logs_filters_by_event(admin_token, tmp_path, monkeypatch):
    log_path = tmp_path / "audit.jsonl"
    _write_audit_log(
        log_path,
        [
            {"event": "webhook.graph_invoke", "level": "info", "ts": "2026-06-22T10:00:00Z"},
            {"event": "webhook.graph_failed", "level": "error", "ts": "2026-06-22T11:00:00Z"},
        ],
    )
    monkeypatch.setattr(
        "praxis.webhooks.logs._audit_log_path", lambda: log_path
    )

    client = TestClient(app)
    resp = client.get(
        "/admin/logs/audit?event=graph_failed",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 1
    assert data["entries"][0]["event"] == "webhook.graph_failed"


def test_audit_logs_filters_by_level(admin_token, tmp_path, monkeypatch):
    log_path = tmp_path / "audit.jsonl"
    _write_audit_log(
        log_path,
        [
            {"event": "webhook.graph_invoke", "level": "info", "ts": "2026-06-22T10:00:00Z"},
            {"event": "webhook.graph_failed", "level": "error", "ts": "2026-06-22T11:00:00Z"},
        ],
    )
    monkeypatch.setattr(
        "praxis.webhooks.logs._audit_log_path", lambda: log_path
    )

    client = TestClient(app)
    resp = client.get(
        "/admin/logs/audit?level=error",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 1
    assert data["entries"][0]["level"] == "error"


def test_audit_logs_filters_by_since(admin_token, tmp_path, monkeypatch):
    """Entries with ts < since are excluded."""
    log_path = tmp_path / "audit.jsonl"
    _write_audit_log(
        log_path,
        [
            {"event": "webhook.graph_invoke", "level": "info", "ts": "2026-06-22T10:00:00Z"},
            {"event": "webhook.graph_failed", "level": "error", "ts": "2026-06-22T11:00:00Z"},
        ],
    )
    monkeypatch.setattr(
        "praxis.webhooks.logs._audit_log_path", lambda: log_path
    )

    client = TestClient(app)
    resp = client.get(
        "/admin/logs/audit?since=2026-06-22T10:30:00Z",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 1
    assert data["entries"][0]["event"] == "webhook.graph_failed"


def test_audit_logs_respects_limit(admin_token, tmp_path, monkeypatch):
    """The limit parameter caps the number of returned entries."""
    log_path = tmp_path / "audit.jsonl"
    entries = [
        {"event": f"event.{i}", "level": "info", "ts": f"2026-06-22T{i:02d}:00:00Z"}
        for i in range(10)
    ]
    _write_audit_log(log_path, entries)
    monkeypatch.setattr(
        "praxis.webhooks.logs._audit_log_path", lambda: log_path
    )

    client = TestClient(app)
    resp = client.get(
        "/admin/logs/audit?limit=3",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 3


def test_logs_tail_requires_auth(admin_token):
    """The SSE tail endpoint also requires admin auth."""
    client = TestClient(app)
    resp = client.get("/admin/logs/tail")
    assert resp.status_code == 401
