"""Tests for admin bearer-token authentication."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
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


def _build_app_with_admin_route():
    """Attach a test route that uses the dependency."""
    from fastapi import APIRouter, Depends

    from praxis.webhooks.auth import require_admin_token

    router = APIRouter()

    @router.get("/_test/admin")
    async def _test_route(_: None = Depends(require_admin_token)):
        return {"ok": True}

    app.include_router(router)


def test_no_token_returns_401(admin_token):
    _build_app_with_admin_route()
    client = TestClient(app)
    resp = client.get("/_test/admin")
    assert resp.status_code == 401


def test_bad_token_returns_403(admin_token):
    _build_app_with_admin_route()
    client = TestClient(app)
    resp = client.get("/_test/admin", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403


def test_valid_token_returns_200(admin_token):
    _build_app_with_admin_route()
    client = TestClient(app)
    resp = client.get("/_test/admin", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_no_admin_token_configured_returns_500(monkeypatch):
    monkeypatch.delenv("PRAXIS_ADMIN_TOKEN", raising=False)
    reset_settings()
    _build_app_with_admin_route()
    client = TestClient(app)
    resp = client.get("/_test/admin", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 500


def test_short_token_rejected_in_production(monkeypatch):
    """Important #8: in production, PRAXIS_ADMIN_TOKEN must be at least
    32 characters. A short token is rejected with 500 so a misconfigured
    production deploy fails closed.
    """
    monkeypatch.setenv("PRAXIS_ADMIN_TOKEN", "short")
    monkeypatch.setenv("ENVIRONMENT", "production")
    reset_settings()
    _build_app_with_admin_route()
    client = TestClient(app)
    resp = client.get(
        "/_test/admin", headers={"Authorization": "Bearer short"}
    )
    assert resp.status_code == 500
    assert "32" in resp.json()["detail"]


def test_short_token_allowed_in_development(monkeypatch):
    """Important #8: the 32-char minimum is only enforced in production
    so dev/test ergonomics are preserved (tests use short tokens).
    """
    monkeypatch.setenv("PRAXIS_ADMIN_TOKEN", "short")
    monkeypatch.setenv("ENVIRONMENT", "development")
    reset_settings()
    _build_app_with_admin_route()
    client = TestClient(app)
    resp = client.get(
        "/_test/admin", headers={"Authorization": "Bearer short"}
    )
    # In development, the short token is accepted (no 32-char check).
    assert resp.status_code == 200


def test_32_char_token_accepted_in_production(monkeypatch):
    """A token of exactly 32 chars is accepted in production."""
    token = "a" * 32  # exactly 32 chars
    monkeypatch.setenv("PRAXIS_ADMIN_TOKEN", token)
    monkeypatch.setenv("ENVIRONMENT", "production")
    reset_settings()
    _build_app_with_admin_route()
    client = TestClient(app)
    resp = client.get(
        "/_test/admin", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
