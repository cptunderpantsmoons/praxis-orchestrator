"""Tests for v0.2.0 config additions."""
from __future__ import annotations

import pytest

from praxis.config import Settings, reset_settings


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


def test_agentmail_inbox_id_default_none():
    settings = Settings()
    assert settings.agentmail_inbox_id is None


def test_agentmail_inbox_id_from_env(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_INBOX_ID", "ib_test_123")
    settings = Settings()
    assert settings.agentmail_inbox_id == "ib_test_123"


def test_praxis_admin_token_default_empty():
    settings = Settings()
    assert settings.praxis_admin_token == ""


def test_tool_protocol_default_native():
    settings = Settings()
    assert settings.tool_protocol == "native"


def test_tool_protocol_legacy(monkeypatch):
    monkeypatch.setenv("TOOL_PROTOCOL", "legacy")
    settings = Settings()
    assert settings.tool_protocol == "legacy"


def test_tool_protocol_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TOOL_PROTOCOL", "bogus")
    with pytest.raises(Exception):
        Settings()


def test_sender_style_enabled_default_true():
    settings = Settings()
    assert settings.sender_style_enabled is True


def test_default_tone():
    settings = Settings()
    assert settings.default_tone == "professional"


def test_default_signature():
    settings = Settings()
    assert settings.default_signature == "— PRAXIS"


def test_praxis_api_url_default():
    settings = Settings()
    assert settings.praxis_api_url == "http://localhost:8000"
