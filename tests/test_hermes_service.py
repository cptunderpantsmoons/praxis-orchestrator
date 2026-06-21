"""Tests for the Hermes memory & learning service (REQ-309).

Tests cover:
- HermesService.recall() with mocked Neo4j + Qdrant + LLM
- HermesService.store() with mocked backends
- HermesService.learn() with mocked LLM extraction + Neo4j upsert
- _execute_tool dispatch for hermes_recall / hermes_store / hermes_learn
- Graceful degradation when backends are unavailable
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from praxis.models.schemas import (
    HermesLearnResult,
    HermesRecallResult,
    HermesStoreResult,
)
from praxis.services.hermes_service import HermesService

# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def mock_neo4j():
    """Mock Neo4j client with sender history + corrections."""
    client = AsyncMock()
    client._driver = MagicMock()
    client._driver.session = MagicMock(return_value=AsyncMock())

    # fetch_sender_history returns ThreadSummary-like objects
    history_item = MagicMock()
    history_item.subject = "Q3 Invoice Review"
    history_item.timestamp.isoformat.return_value = "2026-06-20T10:00:00Z"
    client.fetch_sender_history = AsyncMock(return_value=[history_item])

    # fetch_relevant_corrections returns CorrectionSummary-like objects
    correction = MagicMock()
    correction.rule = "Client prefers PDF invoices"
    correction.confidence = 0.95
    client.fetch_relevant_corrections = AsyncMock(return_value=[correction])

    # upsert_correction returns True
    client.upsert_correction = AsyncMock(return_value=True)
    return client


@pytest.fixture
def mock_qdrant():
    """Mock Qdrant client."""
    client = AsyncMock()
    similar = {
        "payload": {"email": "similar@example.com"},
        "score": 0.92,
    }
    client.get_similar_senders = AsyncMock(return_value=[similar])
    client.upsert_sender = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_embedding_service():
    """Mock embedding service returning a fixed 384-dim vector."""
    es = AsyncMock()
    es.embed_sender = AsyncMock(return_value=[0.1] * 384)
    return es


@pytest.fixture
def mock_router():
    """Mock UmansConcurrencyRouter."""
    return MagicMock()


@pytest.fixture
def hermes(mock_neo4j, mock_qdrant, mock_embedding_service, mock_router):
    """HermesService with all dependencies mocked."""
    return HermesService(
        neo4j_client=mock_neo4j,
        qdrant_client=mock_qdrant,
        embedding_service=mock_embedding_service,
        router=mock_router,
    )


# ── recall() tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recall_returns_graph_paths_and_vector_matches(hermes, mock_neo4j, mock_qdrant):
    """recall() queries Neo4j + Qdrant and returns synthesised results."""
    # Mock the LLM synthesis to just return a fixed string
    with patch.object(hermes, "_synthesise_recall", new_callable=AsyncMock) as mock_synth:
        mock_synth.return_value = "Client has 1 past thread about invoices."
        result = await hermes.recall(
            "invoice history",
            context={"sender": "client@example.com"},
        )

    assert "context_summary" in result
    assert result["context_summary"] == "Client has 1 past thread about invoices."
    assert len(result["graph_paths"]) >= 1  # at least the thread + correction
    assert len(result["vector_matches"]) >= 1  # at least the similar sender
    mock_neo4j.fetch_sender_history.assert_called_once()
    mock_qdrant.get_similar_senders.assert_called_once()


@pytest.mark.asyncio
async def test_recall_degrades_gracefully_without_neo4j(mock_qdrant, mock_embedding_service, mock_router):
    """recall() works with Qdrant alone if Neo4j is unavailable."""
    hermes = HermesService(
        neo4j_client=None,
        qdrant_client=mock_qdrant,
        embedding_service=mock_embedding_service,
        router=mock_router,
    )
    # Force _get_neo4j to return None
    with patch.object(hermes, "_get_neo4j", new_callable=AsyncMock, return_value=None):
        with patch.object(hermes, "_synthesise_recall", new_callable=AsyncMock) as mock_synth:
            mock_synth.return_value = "Partial recall."
            result = await hermes.recall("test query")

    assert result["context_summary"] == "Partial recall."
    assert len(result["graph_paths"]) == 0  # no Neo4j
    assert len(result["vector_matches"]) >= 1  # Qdrant worked


@pytest.mark.asyncio
async def test_recall_no_results_returns_no_memory_message(hermes):
    """recall() returns a 'no memory' summary when nothing is found."""
    with patch.object(hermes, "_get_neo4j", new_callable=AsyncMock, return_value=None):
        with patch.object(hermes, "_get_qdrant", new_callable=AsyncMock, return_value=None):
            result = await hermes.recall("nonexistent")

    assert "No relevant memory" in result["context_summary"]


# ── store() tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_persists_to_neo4j_and_qdrant(hermes, mock_neo4j, mock_qdrant):
    """store() writes to both backends and returns fact_id."""
    result = await hermes.store(
        "Client prefers PDF invoices",
        metadata={"sender": "client@example.com"},
    )

    assert result["stored_neo4j"] is True
    assert result["stored_qdrant"] is True
    assert result["fact_id"]  # non-empty UUID
    mock_qdrant.upsert_sender.assert_called_once()


@pytest.mark.asyncio
async def test_store_without_sender_skips_neo4j(hermes, mock_qdrant):
    """store() without a sender in metadata skips Neo4j but stores to Qdrant."""
    result = await hermes.store("Generic fact")

    assert result["stored_neo4j"] is False
    assert result["stored_qdrant"] is True
    assert result["fact_id"]


@pytest.mark.asyncio
async def test_store_qdrant_failure_returns_partial(hermes, mock_neo4j, mock_qdrant):
    """store() reports partial success if Qdrant fails."""
    mock_qdrant.upsert_sender = AsyncMock(return_value=False)
    result = await hermes.store(
        "test fact",
        metadata={"sender": "client@example.com"},
    )

    assert result["stored_neo4j"] is True
    assert result["stored_qdrant"] is False


# ── learn() tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_learn_extracts_and_upserts_corrections(hermes, mock_neo4j):
    """learn() uses LLM to extract facts and upserts to Neo4j."""
    with patch.object(hermes, "_extract_corrections", new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = [
            "Client prefers PDF invoices",
            "Client expects replies within 2 hours",
        ]
        result = await hermes.learn(
            "Correction: The client prefers PDF invoices and wants replies within 2 hours."
        )

    assert len(result["extracted_facts"]) == 2
    assert result["corrections_applied"] == 2
    assert mock_neo4j.upsert_correction.call_count == 2


@pytest.mark.asyncio
async def test_learn_fallback_returns_single_fact(hermes):
    """learn() returns the raw text as a single fact if LLM extraction fails."""
    with patch.object(hermes, "_get_router", new_callable=AsyncMock, side_effect=Exception("no router")):
        result = await hermes.learn("Some correction text")

    assert len(result["extracted_facts"]) == 1
    assert "Some correction text" in result["extracted_facts"][0]


# ── _execute_tool dispatch tests ────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_hermes_recall(hermes):
    """_execute_tool dispatches hermes_recall correctly."""
    from praxis.graph.nodes import _execute_tool

    with patch.object(hermes, "recall", new_callable=AsyncMock) as mock_recall:
        mock_recall.return_value = {
            "context_summary": "Test summary",
            "graph_paths": ["path1"],
            "vector_matches": ["match1"],
        }
        name, result = await _execute_tool(
            {"name": "hermes_recall", "query": "test query"},
            hermes_service=hermes,
        )

    assert name == "hermes_recall"
    assert isinstance(result, HermesRecallResult)
    assert result.context_summary == "Test summary"
    assert result.match_count == 2
    assert result.success is True


@pytest.mark.asyncio
async def test_execute_tool_hermes_store(hermes):
    """_execute_tool dispatches hermes_store correctly."""
    from praxis.graph.nodes import _execute_tool

    with patch.object(hermes, "store", new_callable=AsyncMock) as mock_store:
        mock_store.return_value = {
            "stored_neo4j": True,
            "stored_qdrant": True,
            "fact_id": "test-123",
        }
        name, result = await _execute_tool(
            {"name": "hermes_store", "fact": "test fact"},
            hermes_service=hermes,
        )

    assert name == "hermes_store"
    assert isinstance(result, HermesStoreResult)
    assert result.fact == "test fact"
    assert result.stored_neo4j is True
    assert result.stored_qdrant is True
    assert result.fact_id == "test-123"


@pytest.mark.asyncio
async def test_execute_tool_hermes_learn(hermes):
    """_execute_tool dispatches hermes_learn correctly."""
    from praxis.graph.nodes import _execute_tool

    with patch.object(hermes, "learn", new_callable=AsyncMock) as mock_learn:
        mock_learn.return_value = {
            "extracted_facts": ["fact1"],
            "corrections_applied": 1,
        }
        name, result = await _execute_tool(
            {"name": "hermes_learn", "correction": "fix this"},
            hermes_service=hermes,
        )

    assert name == "hermes_learn"
    assert isinstance(result, HermesLearnResult)
    assert result.correction_text == "fix this"
    assert len(result.extracted_facts) == 1
    assert result.corrections_applied == 1


@pytest.mark.asyncio
async def test_execute_tool_hermes_recall_without_service():
    """_execute_tool returns graceful degradation when hermes_service is None."""
    from praxis.graph.nodes import _execute_tool

    name, result = await _execute_tool(
        {"name": "hermes_recall", "query": "test"},
        hermes_service=None,
    )

    assert name == "hermes_recall"
    assert isinstance(result, HermesRecallResult)
    assert result.success is False
    assert "unavailable" in result.context_summary.lower()


# ── Schema validation tests ────────────────────────────────────


def test_hermes_recall_result_schema():
    """HermesRecallResult validates correctly."""
    result = HermesRecallResult(
        query="test",
        context_summary="summary",
        graph_paths=["path1"],
        vector_matches=["match1"],
    )
    assert result.tool_name == "hermes_recall"
    assert result.success is True
    assert result.match_count == 2


def test_hermes_store_result_schema():
    """HermesStoreResult validates correctly."""
    result = HermesStoreResult(
        fact="test fact",
        stored_neo4j=True,
        stored_qdrant=False,
        fact_id="abc-123",
    )
    assert result.tool_name == "hermes_store"
    assert result.success is True  # at least one backend stored


def test_hermes_learn_result_schema():
    """HermesLearnResult validates correctly."""
    result = HermesLearnResult(
        correction_text="fix this",
        extracted_facts=["fact1", "fact2"],
        corrections_applied=2,
    )
    assert result.tool_name == "hermes_learn"
    assert result.success is True
    assert len(result.extracted_facts) == 2
