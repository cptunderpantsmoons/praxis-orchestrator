"""Shared pytest fixtures for PRAXIS v2.0 tests."""

from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from praxis.config import Settings
from praxis.router import UmansConcurrencyRouter
from praxis.webhooks.svix import compute_svix_signature

# Test webhook secret — base64 for "testsecret"
TEST_SECRET = "whsec_dGVzdHNlY3JldA=="


# ── Settings & Mocks ────────────────────────────────────────────


@pytest.fixture
def test_settings() -> Settings:
    """Settings with test values."""
    return Settings(
        environment="test",
        umans_api_key="test-key",
        umans_base_url="https://test.umans.ai/v1",
        agentmail_webhook_secret=TEST_SECRET,
    )


@pytest.fixture
def mock_umans_response() -> dict:
    """A minimal OpenAI-compatible chat completion response."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "umans-flash",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Test response"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.fixture
def mock_transport(mock_umans_response):
    """httpx MockTransport that returns a canned response."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_umans_response)

    return httpx.MockTransport(handler)


@pytest.fixture
def router(test_settings, mock_transport) -> UmansConcurrencyRouter:
    """A router with a mock HTTP client for testing."""
    client = httpx.AsyncClient(transport=mock_transport, base_url="http://test")
    return UmansConcurrencyRouter(settings=test_settings, http_client=client)


# ── Svix Signing Helper ─────────────────────────────────────────


@pytest.fixture
def svix_signer():
    """Helper to create valid Svix signatures for testing."""

    def _sign(
        raw_body: bytes,
        secret: str = TEST_SECRET,
        timestamp: int | None = None,
    ) -> dict[str, str]:
        ts = timestamp or int(time.time())
        sig = compute_svix_signature(raw_body, "msg_test123", str(ts), secret)
        return {
            "svix-id": "msg_test123",
            "svix-timestamp": str(ts),
            "svix-signature": sig,
        }

    return _sign


# ── FastAPI TestClient ──────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    """FastAPI TestClient with test settings."""
    monkeypatch.setenv("AGENTMAIL_WEBHOOK_SECRET", TEST_SECRET)

    # Clear cached settings so the lifespan picks up the test env var
    from praxis.config import get_settings

    get_settings.cache_clear()

    from praxis.main import app

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()
