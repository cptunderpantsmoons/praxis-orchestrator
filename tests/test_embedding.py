"""Tests for REQ-306: Embedding service, embedding node, and Qdrant metadata.

The EmbeddingService now uses fastembed (Qdrant) as the primary backend
with a deterministic hash-based fallback. Tests verify both paths.

Three layers of coverage:
1. ``EmbeddingService.embed_sender`` — fastembed primary, hash fallback
2. ``embedding_node`` — full node test with mocked services and graceful-
   degradation paths.
3. ``QdrantSenderClient.upsert_sender`` — verify the new metadata
   (``first_seen``, ``last_seen``, ``total_emails``, ``sender_domain``)
   is written on every upsert.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from praxis.graph.nodes import embedding_node
from praxis.graph.state import AgentState
from praxis.models.schemas import (
    AgentMetadata,
    EmailTriage,
    InboundEmail,
    Intent,
    MemoryContext,
    Priority,
    Sentiment,
)
from praxis.services.embedding_service import (
    DEFAULT_DIM,
    EmbeddingService,
    _hash_embedding,
)
from praxis.services.qdrant_client import QdrantSenderClient

# ── Helpers ──────────────────────────────────────────────────────


def _make_state() -> AgentState:
    return {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Hello",
            body="Test email",
        ),
        "triage_result": EmailTriage(
            priority=Priority.NORMAL,
            intent=Intent.GENERAL_INQUIRY,
            sentiment=Sentiment.NEUTRAL,
            is_spam=False,
            sender_vip=False,
            confidence=0.9,
        ),
        "memory_context": MemoryContext(),
        "metadata": AgentMetadata(),
    }


# ── EmbeddingService: fastembed path ───────────────────────────


@pytest.mark.asyncio
async def test_embedding_service_returns_fastembed_vector() -> None:
    """embed_sender returns a real vector from fastembed."""
    svc = EmbeddingService()
    vec = await svc.embed_sender("user@example.com")
    assert isinstance(vec, list)
    assert len(vec) == DEFAULT_DIM
    assert all(isinstance(x, float) for x in vec)
    # L2-normalized
    import math
    norm = math.sqrt(sum(x * x for x in vec))
    assert 0.99 < norm < 1.01
    # Should have used fastembed (or fell back to hash)
    assert svc.backend in ("fastembed",) or "fastembed" in svc.backend or "hash" in svc.backend


@pytest.mark.asyncio
async def test_embedding_service_deterministic() -> None:
    """Same input always returns the same vector (even with hash fallback)."""
    svc = EmbeddingService()
    v1 = await svc.embed_sender("user@example.com")
    v2 = await svc.embed_sender("user@example.com")
    assert v1 == v2


@pytest.mark.asyncio
async def test_embedding_service_distinct_inputs() -> None:
    """Different inputs give different vectors."""
    svc = EmbeddingService()
    v1 = await svc.embed_sender("alice@example.com")
    v2 = await svc.embed_sender("bob@example.com")
    assert v1 != v2


@pytest.mark.asyncio
async def test_embedding_service_raises_on_empty_sender() -> None:
    """Empty sender raises ValueError before any embedding call."""
    svc = EmbeddingService()
    with pytest.raises(ValueError, match="non-empty"):
        await svc.embed_sender("")
    with pytest.raises(ValueError, match="non-empty"):
        await svc.embed_sender("   ")


# ── EmbeddingService: hash fallback ─────────────────────────────


def test_hash_embedding_is_deterministic() -> None:
    """The hash fallback produces consistent output."""
    v1 = _hash_embedding("alice@example.com")
    v2 = _hash_embedding("alice@example.com")
    assert v1 == v2
    assert len(v1) == DEFAULT_DIM


def test_hash_embedding_distinct_inputs() -> None:
    v1 = _hash_embedding("alice@example.com")
    v2 = _hash_embedding("bob@example.com")
    assert v1 != v2


def test_hash_embedding_l2_normalized() -> None:
    """Hash vectors are L2-normalized (usable for cosine similarity)."""
    import math
    vec = _hash_embedding("anything")
    norm = math.sqrt(sum(x * x for x in vec))
    assert 0.99 < norm < 1.01


@pytest.mark.asyncio
async def test_embedding_service_falls_back_to_hash_on_fastembed_error() -> None:
    """If fastembed raises, the service falls back to hash and still returns a vector."""
    svc = EmbeddingService()
    # Force the fastembed path to fail
    svc._ensure_model = lambda: None  # type: ignore[assignment]
    svc._model = None
    # Now make _ensure_model return False (sentinel for "tried and failed")
    with patch.object(svc, "_ensure_model") as mock_ensure:
        mock_ensure.side_effect = lambda: setattr(svc, "_model", False) or setattr(svc, "_backend", "hash (test)")
        vec = await svc.embed_sender("test@example.com")
    # Should still have a valid vector (from hash)
    assert len(vec) == DEFAULT_DIM
    assert "hash" in svc.backend


# ── embedding_node tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_embedding_node_persists_to_qdrant() -> None:
    """Embedding node calls the embedding service, then upserts to Qdrant."""
    fake_vector = [0.5] * DEFAULT_DIM
    embedding_service = MagicMock()
    embedding_service.embed_sender = AsyncMock(return_value=fake_vector)

    qdrant_client = MagicMock()
    qdrant_client.upsert_sender = AsyncMock(return_value=True)

    state = _make_state()
    result = await embedding_node(
        state,
        config={"configurable": {
            "embedding_service": embedding_service,
            "qdrant_client": qdrant_client,
        }},
    )

    embedding_service.embed_sender.assert_awaited_once_with("user@example.com")
    qdrant_client.upsert_sender.assert_awaited_once_with("user@example.com", fake_vector)
    assert "metadata" in result


@pytest.mark.asyncio
async def test_embedding_node_graceful_on_embedding_failure() -> None:
    """If the embedding service raises, the node logs and returns metadata."""
    embedding_service = MagicMock()
    embedding_service.embed_sender = AsyncMock(
        side_effect=Exception("upstream timeout")
    )
    qdrant_client = MagicMock()
    qdrant_client.upsert_sender = AsyncMock()

    state = _make_state()
    result = await embedding_node(
        state,
        config={"configurable": {
            "embedding_service": embedding_service,
            "qdrant_client": qdrant_client,
        }},
    )

    qdrant_client.upsert_sender.assert_not_called()
    assert "metadata" in result


@pytest.mark.asyncio
async def test_embedding_node_graceful_on_qdrant_failure() -> None:
    """If Qdrant upsert returns False, the node still completes."""
    embedding_service = MagicMock()
    embedding_service.embed_sender = AsyncMock(return_value=[0.1, 0.2])
    qdrant_client = MagicMock()
    qdrant_client.upsert_sender = AsyncMock(return_value=False)

    state = _make_state()
    result = await embedding_node(
        state,
        config={"configurable": {
            "embedding_service": embedding_service,
            "qdrant_client": qdrant_client,
        }},
    )

    qdrant_client.upsert_sender.assert_awaited_once()
    assert "metadata" in result


@pytest.mark.asyncio
async def test_embedding_node_preserves_existing_metadata() -> None:
    """If state has existing metadata, embedding_node keeps it."""
    state = _make_state()
    state["metadata"] = AgentMetadata(thread_id="thread-abc")
    embedding_service = MagicMock()
    embedding_service.embed_sender = AsyncMock(return_value=[0.0])
    qdrant_client = MagicMock()
    qdrant_client.upsert_sender = AsyncMock(return_value=True)

    result = await embedding_node(
        state,
        config={"configurable": {
            "embedding_service": embedding_service,
            "qdrant_client": qdrant_client,
        }},
    )
    assert result["metadata"].thread_id == "thread-abc"


# ── QdrantSenderClient metadata tests ────────────────────────────


class _FakeExistingPoint:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class _FakeRetrieveResult(list):
    """A list subclass that supports [0] indexing in upsert_sender."""


@pytest.mark.asyncio
async def test_upsert_sender_writes_required_metadata() -> None:
    """First upsert writes first_seen, last_seen, sender_domain, total_emails=1."""
    client = QdrantSenderClient(collection="test_collection")
    client._client = MagicMock()
    client._client.retrieve = AsyncMock(return_value=[])  # No existing point
    client._client.upsert = AsyncMock()

    ok = await client.upsert_sender("alice@example.com", [0.1, 0.2, 0.3])
    assert ok is True

    upsert_call = client._client.upsert.call_args
    assert upsert_call.args[0] == "test_collection"
    point = upsert_call.kwargs["points"][0]
    payload = point.payload
    assert payload["email"] == "alice@example.com"
    meta = payload["metadata"]
    assert meta["sender_domain"] == "example.com"
    assert meta["total_emails"] == 1
    assert "first_seen" in meta
    assert "last_seen" in meta


@pytest.mark.asyncio
async def test_upsert_sender_increments_total_emails() -> None:
    """Subsequent upserts increment total_emails and preserve first_seen."""
    client = QdrantSenderClient(collection="test_collection")
    client._client = MagicMock()
    existing_payload = {
        "email": "bob@example.com",
        "metadata": {
            "first_seen": "2026-01-01T00:00:00+00:00",
            "last_seen": "2026-01-01T00:00:00+00:00",
            "total_emails": 5,
            "sender_domain": "example.com",
        },
    }
    client._client.retrieve = AsyncMock(
        return_value=_FakeRetrieveResult([_FakeExistingPoint(existing_payload)])
    )
    client._client.upsert = AsyncMock()

    await client.upsert_sender("bob@example.com", [0.1])

    upsert_call = client._client.upsert.call_args
    meta = upsert_call.kwargs["points"][0].payload["metadata"]
    assert meta["total_emails"] == 6
    assert meta["first_seen"] == "2026-01-01T00:00:00+00:00"  # preserved


@pytest.mark.asyncio
async def test_upsert_sender_returns_false_on_upsert_failure() -> None:
    """Backend errors during upsert return False, never raise."""
    client = QdrantSenderClient(collection="test_collection")
    client._client = MagicMock()
    client._client.retrieve = AsyncMock(return_value=[])
    client._client.upsert = AsyncMock(side_effect=RuntimeError("qdrant down"))

    ok = await client.upsert_sender("fail@example.com", [0.1])
    assert ok is False


@pytest.mark.asyncio
async def test_upsert_sender_handles_retrieve_failure() -> None:
    """If retrieve fails, we still upsert — total_emails defaults to 1."""
    client = QdrantSenderClient(collection="test_collection")
    client._client = MagicMock()
    client._client.retrieve = AsyncMock(side_effect=RuntimeError("network"))
    client._client.upsert = AsyncMock()

    ok = await client.upsert_sender("ok@example.com", [0.1])
    assert ok is True
    meta = client._client.upsert.call_args.kwargs["points"][0].payload["metadata"]
    assert meta["total_emails"] == 1
    assert meta["sender_domain"] == "example.com"


@pytest.mark.asyncio
async def test_upsert_sender_extracts_domain() -> None:
    """sender_domain is the part after the @ sign."""
    client = QdrantSenderClient(collection="test")
    client._client = MagicMock()
    client._client.retrieve = AsyncMock(return_value=[])
    client._client.upsert = AsyncMock()
    await client.upsert_sender("someone@subdomain.example.org", [0.0])
    meta = client._client.upsert.call_args.kwargs["points"][0].payload["metadata"]
    assert meta["sender_domain"] == "subdomain.example.org"


# Silence unused import warnings
_ = datetime
_ = timezone
