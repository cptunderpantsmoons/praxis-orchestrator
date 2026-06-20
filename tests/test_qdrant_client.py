"""Tests for the Qdrant sender client.

Tests upsert, search, history retrieval, and health check using pytest-mock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from praxis.services.qdrant_client import QdrantSenderClient


@pytest.fixture
def mock_qdrant() -> MagicMock:
    """Mock AsyncQdrantClient for testing."""
    mock = MagicMock()
    # Make all methods async
    mock.upsert = AsyncMock(return_value=None)
    mock.search = AsyncMock(return_value=[])
    mock.scroll = AsyncMock(return_value=([], None))
    mock.delete = AsyncMock(return_value=None)
    mock.get_collections = AsyncMock(return_value=MagicMock(collections=[]))
    mock.create_collection = AsyncMock(return_value=None)
    mock.close = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def client(mock_qdrant: MagicMock) -> QdrantSenderClient:
    """QdrantSenderClient with mocked AsyncQdrantClient."""
    c = QdrantSenderClient(url="http://mock-qdrant:6333")
    c._client = mock_qdrant
    c.timeout = 1.0
    return c


# ── Tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_sender_succeeds(client: QdrantSenderClient) -> None:
    """upsert_sender calls AsyncQdrantClient.upsert and returns True."""
    result = await client.upsert_sender(
        email="test@example.com",
        embedding=[0.1] * 1536,
    )
    assert result is True
    client._client.upsert.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_sender_handles_failure(client: QdrantSenderClient) -> None:
    """upsert_sender returns False when Qdrant raises an exception."""
    client._client.upsert = AsyncMock(side_effect=Exception("connection lost"))
    result = await client.upsert_sender(
        email="fail@example.com",
        embedding=[0.1] * 1536,
    )
    assert result is False


@pytest.mark.asyncio
async def test_get_similar_senders_returns_list(client: QdrantSenderClient) -> None:
    """get_similar_senders searches Qdrant and returns a list of dicts."""
    mock_result = MagicMock()
    mock_result.payload = {"email": "similar@example.com", "metadata": {}}
    mock_result.score = 0.95
    client._client.search = AsyncMock(return_value=[mock_result])

    results = await client.get_similar_senders([0.1] * 1536, limit=5)
    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["email"] == "similar@example.com"
    assert results[0]["score"] == 0.95


@pytest.mark.asyncio
async def test_get_sender_history_returns_list(client: QdrantSenderClient) -> None:
    """get_sender_history scrolls Qdrant and returns a list of payloads."""
    mock_point = MagicMock()
    mock_point.payload = {"email": "test@example.com"}
    # scroll returns (list of (point, score) tuples, next_offset)
    client._client.scroll = AsyncMock(return_value=([(mock_point, None)], None))

    results = await client.get_sender_history("test@example.com", limit=10)
    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_delete_sender_succeeds(client: QdrantSenderClient) -> None:
    """delete_sender calls AsyncQdrantClient.delete and returns True."""
    result = await client.delete_sender("test@example.com")
    assert result is True
    client._client.delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_sender_handles_failure(client: QdrantSenderClient) -> None:
    """delete_sender returns False when Qdrant raises an exception."""
    client._client.delete = AsyncMock(side_effect=Exception("connection lost"))
    result = await client.delete_sender("fail@example.com")
    assert result is False


@pytest.mark.asyncio
async def test_health_check_returns_true_when_collection_exists(client: QdrantSenderClient) -> None:
    """health_check returns True when the collection exists."""
    mock_collection = MagicMock()
    mock_collection.name = "praxis_senders"
    client._client.get_collections = AsyncMock(
        return_value=MagicMock(collections=[mock_collection])
    )
    result = await client.health_check()
    assert result is True


@pytest.mark.asyncio
async def test_health_check_returns_false_when_collection_missing(client: QdrantSenderClient) -> None:
    """health_check returns False when collection doesn't exist."""
    client._client.get_collections = AsyncMock(
        return_value=MagicMock(collections=[])
    )
    result = await client.health_check()
    assert result is False


@pytest.mark.asyncio
async def test_ensure_client_creates_collection(client: QdrantSenderClient) -> None:
    """_ensure_client creates collection if not exists."""
    # Force reset so _ensure_client runs
    client._client = None
    mock_qdrant = MagicMock()
    mock_qdrant.get_collections = AsyncMock(return_value=MagicMock(collections=[]))
    mock_qdrant.create_collection = AsyncMock(return_value=None)
    
    with patch("praxis.services.qdrant_client.AsyncQdrantClient") as mock_client_cls:
        mock_client_cls.return_value = mock_qdrant
        await client._ensure_client()
    
    mock_qdrant.create_collection.assert_called_once()
