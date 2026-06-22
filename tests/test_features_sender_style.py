"""Tests for per-sender reply style."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from praxis.config import reset_settings
from praxis.features.sender_style import SenderStyle, get_style_for_sender, update_sender_style_tool


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


def test_sender_style_defaults():
    s = SenderStyle()
    assert s.tone == "professional"
    assert s.signature == "— PRAXIS"
    assert s.language is None
    assert s.greeting is None


@pytest.mark.asyncio
async def test_get_style_for_sender_returns_none_when_no_entry(monkeypatch):
    mock_client = MagicMock()
    mock_client.fetch_sender_style = AsyncMock(return_value=None)
    monkeypatch.setattr("praxis.features.sender_style.Neo4jContextClient", lambda: mock_client)
    result = await get_style_for_sender("user@example.com")
    assert result is None


@pytest.mark.asyncio
async def test_get_style_for_sender_returns_stored_style(monkeypatch):
    stored = SenderStyle(tone="casual", signature="Cheers, P", language="en", greeting="Hi there")
    mock_client = MagicMock()
    mock_client.fetch_sender_style = AsyncMock(return_value=stored)
    monkeypatch.setattr("praxis.features.sender_style.Neo4jContextClient", lambda: mock_client)
    result = await get_style_for_sender("user@example.com")
    assert result is not None
    assert result.tone == "casual"
    assert result.signature == "Cheers, P"


@pytest.mark.asyncio
async def test_update_sender_style_tool_writes_to_neo4j(monkeypatch):
    mock_client = MagicMock()
    mock_client.upsert_sender_style = AsyncMock(return_value=None)
    monkeypatch.setattr("praxis.features.sender_style.Neo4jContextClient", lambda: mock_client)
    result = await update_sender_style_tool.ainvoke({
        "tone": "casual",
        "signature": "Cheers",
        "language": "en",
        "greeting": "Hi",
        "sender_email": "user@example.com",
    })
    assert "updated" in result.lower() or "saved" in result.lower()
    mock_client.upsert_sender_style.assert_called_once()
    args = mock_client.upsert_sender_style.call_args
    assert args[0][0] == "user@example.com" or args.kwargs.get("sender_email") == "user@example.com"
