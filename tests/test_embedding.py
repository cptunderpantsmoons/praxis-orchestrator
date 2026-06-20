"""Tests for REQ-306: Embedding service, embedding node, and Qdrant metadata.

Three layers of coverage:
1. ``EmbeddingService.embed_sender`` — direct unit tests with mocked router.
2. ``embedding_node`` — full node test with mocked services and graceful-
   degradation paths.
3. ``QdrantSenderClient.upsert_sender`` — verify the new metadata
   (``first_seen``, ``last_seen``, ``total_emails``, ``sender_domain``)
   is written on every upsert, and that subsequent calls increment
   ``total_emails`` rather than resetting it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

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
    EmbeddingService,
    EmbeddingServiceError,
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


# ── EmbeddingService unit tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_embedding_service_returns_vector() -> None:
    """embed_sender returns the vector from the router."""
    fake_vector = [0.1, 0.2, 0.3, 0.4]
    router = MagicMock()
    router.embed = AsyncMock(return_value=fake_vector)
    svc = EmbeddingService(router=router, model_name="umans-embed-small")
    vec = await svc.embed_sender("user@example.com")
    assert vec == fake_vector
    router.embed.assert_awaited_once_with("umans-embed-small", "user@example.com")


@pytest.mark.asyncio
async def test_embedding_service_raises_on_empty_sender() -> None:
    """Empty sender raises ValueError before any HTTP call."""
    router = MagicMock()
    router.embed = AsyncMock()
    svc = EmbeddingService(router=router)
    with pytest.raises(ValueError, match="non-empty"):
        await svc.embed_sender("")
    router.embed.assert_not_called()


@pytest.mark.asyncio
async def test_embedding_service_wraps_router_errors() -> None:
    """Underlying router errors are wrapped in EmbeddingServiceError."""
    router = MagicMock()
    router.embed = AsyncMock(side_effect=RuntimeError("HTTP 500"))
    svc = EmbeddingService(router=router)
    with pytest.raises(EmbeddingServiceError) as exc_info:
        await svc.embed_sender("user@example.com")
    assert "Embedding call failed" in str(exc_info.value)
    assert "HTTP 500" in str(exc_info.value)


@pytest.mark.asyncio
async def test_embedding_service_raises_on_empty_vector() -> None:
    """An empty vector from the router is also an error."""
    router = MagicMock()
    router.embed = AsyncMock(return_value=[])
    svc = EmbeddingService(router=router)
    with pytest.raises(EmbeddingServiceError, match="empty vector"):
        await svc.embed_sender("user@example.com")


def test_embedding_service_lazy_creates_router() -> None:
    """If no router is injected, the service creates one on first access."""
    svc = EmbeddingService(model_name="umans-embed-small")
    assert svc._router is None
    r = svc.router
    assert r is not None
    # Subsequent access returns the same instance
    assert svc.router is r


# ── embedding_node tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_embedding_node_persists_to_qdrant() -> None:
    """Embedding node calls the embedding service, then upserts to Qdrant."""
    fake_vector = [0.5] * 4
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
        side_effect=EmbeddingServiceError("upstream timeout")
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


# ── Router embed() tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_router_embed_rejects_non_embed_model() -> None:
    """Router.embed() refuses chat models."""
    from praxis.router import UmansConcurrencyRouter

    router = UmansConcurrencyRouter()
    with pytest.raises(ValueError, match="not an embedding model"):
        await router.embed("umans-flash", "hello")


@pytest.mark.asyncio
async def test_router_embed_calls_endpoint() -> None:
    """Router.embed() posts to /embeddings and returns a list of floats."""
    import httpx

    from praxis.router import UmansConcurrencyRouter

    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.read()
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://mock")
    router = UmansConcurrencyRouter(http_client=client)

    vec = await router.embed("umans-embed-small", "test text")
    assert vec == [0.1, 0.2, 0.3, 0.4]
    assert captured["path"] == "/embeddings"


@pytest.mark.asyncio
async def test_router_embed_handles_empty_data() -> None:
    """Empty data array raises ValueError."""
    import httpx

    from praxis.router import UmansConcurrencyRouter

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://mock")
    router = UmansConcurrencyRouter(http_client=client)
    with pytest.raises(ValueError, match="no data"):
        await router.embed("umans-embed-small", "test")


# Silence unused import warnings
_ = datetime
_ = timezone
