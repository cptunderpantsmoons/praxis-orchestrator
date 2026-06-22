"""Tests for GET/POST /admin/settings."""
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


def test_get_settings_requires_auth(admin_token):
    client = TestClient(app)
    resp = client.get("/admin/settings")
    assert resp.status_code == 401


def test_get_settings_masks_secrets(admin_token, monkeypatch):
    """Secret fields are masked; non-secret fields are visible."""
    monkeypatch.setenv("UMANS_API_KEY", "real-umans-key")
    monkeypatch.setenv("NEO4J_PASSWORD", "real-neo4j-pass")
    monkeypatch.setenv("UMANS_BASE_URL", "https://example.umans.ai/v1")
    reset_settings()
    client = TestClient(app)
    resp = client.get(
        "/admin/settings", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["umans_api_key"] == "***"
    assert data["neo4j_password"] == "***"
    assert "umans_base_url" in data
    assert data["umans_base_url"] != "***"
    assert data["umans_base_url"] == "https://example.umans.ai/v1"


def test_get_settings_empty_secret_shows_empty_string(admin_token, monkeypatch):
    """If a secret is unset, the masked value is the empty string (not '***')."""
    # Default agentmail_api_key is "test-key" (a non-empty value); override to empty
    monkeypatch.setenv("AGENTMAIL_API_KEY", "")
    reset_settings()
    client = TestClient(app)
    resp = client.get(
        "/admin/settings", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["agentmail_api_key"] == ""


def test_post_settings_requires_auth(admin_token):
    client = TestClient(app)
    resp = client.post("/admin/settings", json={"environment": "staging"})
    assert resp.status_code == 401


def test_post_settings_updates_env_file(admin_token, monkeypatch, tmp_path):
    """A POST to /admin/settings writes the new value to the .env file."""
    env_path = tmp_path / ".env"
    env_path.write_text("UMANS_BASE_URL=https://old.example.com\n")
    # Patch the env-file path resolver in the settings module to use tmp_path
    monkeypatch.setattr(
        "praxis.webhooks.settings_admin._env_file_path",
        lambda: env_path,
    )
    client = TestClient(app)
    resp = client.post(
        "/admin/settings",
        json={"umans_base_url": "https://new.example.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    content = env_path.read_text()
    assert "https://new.example.com" in content


def test_post_settings_treats_mask_as_no_change(admin_token, monkeypatch, tmp_path):
    """When a secret field is sent as '***', the existing value is preserved."""
    env_path = tmp_path / ".env"
    env_path.write_text("UMANS_API_KEY=existing-secret-key\n")
    monkeypatch.setattr(
        "praxis.webhooks.settings_admin._env_file_path",
        lambda: env_path,
    )
    client = TestClient(app)
    resp = client.post(
        "/admin/settings",
        json={"umans_api_key": "***"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    content = env_path.read_text()
    assert "existing-secret-key" in content
    assert "***" not in content


def test_post_settings_rejects_unknown_field(admin_token, monkeypatch, tmp_path):
    """Unknown field names are rejected with a 422."""
    env_path = tmp_path / ".env"
    env_path.write_text("")
    monkeypatch.setattr(
        "praxis.webhooks.settings_admin._env_file_path",
        lambda: env_path,
    )
    client = TestClient(app)
    resp = client.post(
        "/admin/settings",
        json={"definitely_not_a_real_field": "value"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 422
