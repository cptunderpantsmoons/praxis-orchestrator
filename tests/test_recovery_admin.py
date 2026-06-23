"""Tests for the /admin/recovery endpoints."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_token(monkeypatch):
    token = "test-admin-token-1234567890abcdef1234567890"
    monkeypatch.setenv("PRAXIS_ADMIN_TOKEN", token)
    # Reset settings cache so the new token takes effect
    from praxis.config import reset_settings

    reset_settings()
    yield token
    reset_settings()


@pytest.fixture
def app_with_recovery(admin_token, monkeypatch):
    """Create an app instance with a mock recovery manager on state."""
    # Mock the heavy lifespan components to avoid needing real services
    # We import the app fresh and manually set state
    from praxis.main import app

    # Create a mock recovery manager
    mock_mgr = MagicMock()
    mock_mgr.list_events = MagicMock(return_value=[])
    mock_mgr.stats = MagicMock(
        return_value=MagicMock(
            model_dump=MagicMock(
                return_value={
                    "total_failures": 0,
                    "total_recovered": 0,
                    "total_failed": 0,
                    "total_escalated": 0,
                    "by_pattern": {},
                    "by_tool": {},
                    "queue_size": 0,
                    "worker_running": False,
                }
            )
        )
    )
    mock_mgr.health_status = MagicMock(
        return_value={
            "worker_running": True,
            "enabled": True,
            "queue_size": 0,
            "last_processed_at": None,
            "poll_interval_seconds": 10,
            "rate_limit_seconds": 5.0,
            "max_retries": 3,
        }
    )
    mock_mgr.enable = MagicMock()
    mock_mgr.disable = MagicMock()
    app.state.recovery_manager = mock_mgr
    yield app, mock_mgr
    # Cleanup
    if hasattr(app.state, "recovery_manager"):
        del app.state.recovery_manager


class TestAuthRequired:
    def test_events_requires_auth(self, app_with_recovery):
        app, _ = app_with_recovery
        client = TestClient(app)
        resp = client.get("/admin/recovery/events")
        assert resp.status_code == 401

    def test_stats_requires_auth(self, app_with_recovery):
        app, _ = app_with_recovery
        client = TestClient(app)
        resp = client.get("/admin/recovery/stats")
        assert resp.status_code == 401

    def test_health_requires_auth(self, app_with_recovery):
        app, _ = app_with_recovery
        client = TestClient(app)
        resp = client.get("/admin/recovery/health")
        assert resp.status_code == 401

    def test_enable_requires_auth(self, app_with_recovery):
        app, _ = app_with_recovery
        client = TestClient(app)
        resp = client.post("/admin/recovery/enable")
        assert resp.status_code == 401

    def test_disable_requires_auth(self, app_with_recovery):
        app, _ = app_with_recovery
        client = TestClient(app)
        resp = client.post("/admin/recovery/disable")
        assert resp.status_code == 401


class TestEndpoints:
    def test_events_returns_empty_list(self, app_with_recovery, admin_token):
        app, _ = app_with_recovery
        client = TestClient(app)
        resp = client.get(
            "/admin/recovery/events",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["events"] == []

    def test_stats_returns_defaults(self, app_with_recovery, admin_token):
        app, _ = app_with_recovery
        client = TestClient(app)
        resp = client.get(
            "/admin/recovery/stats",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_failures"] == 0
        assert body["worker_running"] is False

    def test_health_returns_config(self, app_with_recovery, admin_token):
        app, _ = app_with_recovery
        client = TestClient(app)
        resp = client.get(
            "/admin/recovery/health",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["worker_running"] is True
        assert body["poll_interval_seconds"] == 10
        assert body["max_retries"] == 3

    def test_enable_calls_manager_enable(self, app_with_recovery, admin_token):
        app, mock_mgr = app_with_recovery
        client = TestClient(app)
        resp = client.post(
            "/admin/recovery/enable",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "enabled"
        mock_mgr.enable.assert_called_once()

    def test_disable_calls_manager_disable(self, app_with_recovery, admin_token):
        app, mock_mgr = app_with_recovery
        client = TestClient(app)
        resp = client.post(
            "/admin/recovery/disable",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"
        mock_mgr.disable.assert_called_once()

    def test_events_with_status_filter(self, app_with_recovery, admin_token):
        app, mock_mgr = app_with_recovery
        # Mock list_events to return one event
        from datetime import UTC, datetime

        from praxis.recovery.models import FailureContext, RecoveryEvent, RecoveryPattern

        mock_event = RecoveryEvent(
            failure=FailureContext(
                tool_name="list_agents",
                error_message="empty",
                args={"region": "AU"},
                timestamp=datetime.now(UTC),
            ),
            pattern=RecoveryPattern.TOOL_RETURNED_EMPTY,
            status="recovered",
        )
        mock_mgr.list_events = MagicMock(return_value=[mock_event])
        client = TestClient(app)
        resp = client.get(
            "/admin/recovery/events?status=recovered&limit=10",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["events"][0]["failure"]["tool_name"] == "list_agents"
        # Verify filter was passed
        mock_mgr.list_events.assert_called_once_with(
            limit=10, status="recovered", pattern=None
        )


class TestNoManager:
    def test_events_returns_503_when_no_manager(self, admin_token, monkeypatch):
        """When recovery_manager is not on app.state, endpoints return 503."""
        from praxis.main import app

        # Ensure no manager is set
        if hasattr(app.state, "recovery_manager"):
            monkeypatch.setattr(app.state, "recovery_manager", None, raising=False)
        client = TestClient(app)
        resp = client.get(
            "/admin/recovery/events",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 503
