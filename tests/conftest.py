"""Shared test fixtures and configuration for PRAXIS tests.

Environment variables are set BEFORE any praxis imports so that
``pydantic-settings`` loads test-friendly values.
"""

import base64
import os
import sys
from pathlib import Path

# ── Set test environment variables BEFORE any praxis imports ─────────
_TEST_KEY_RAW = b"praxis-test-secret-key"
_TEST_KEY_B64 = base64.b64encode(_TEST_KEY_RAW).decode()
TEST_SECRET = f"whsec_{_TEST_KEY_B64}"

os.environ.setdefault("SVIX_WEBHOOK_SECRET", TEST_SECRET)
os.environ.setdefault("UMANS_API_KEY", "test-api-key")
os.environ.setdefault("UMANS_API_BASE_URL", "http://test-umans.local")
os.environ.setdefault("NEO4J_PASSWORD", "test")
os.environ.setdefault("QDRANT_API_KEY", "test")

# Ensure src/ is importable for direct pytest invocation
_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from fastapi.testclient import TestClient

from praxis.main import app
from praxis.webhooks.svix import sign_for_testing


@pytest.fixture
def client():
    """FastAPI TestClient with lifespan (startup/shutdown) support."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def svix_secret() -> str:
    """The test Svix webhook secret."""
    return TEST_SECRET


@pytest.fixture
def make_svix_headers():
    """Factory fixture: create valid Svix headers for a given payload."""

    def _make(
        payload: bytes,
        msg_id: str = "msg_test123",
        *,
        timestamp: int | None = None,
    ) -> dict[str, str]:
        sid, sts, ssig = sign_for_testing(
            payload, msg_id, TEST_SECRET, timestamp=timestamp
        )
        return {
            "svix-id": sid,
            "svix-timestamp": sts,
            "svix-signature": ssig,
        }

    return _make
