"""Tests for the correction node.

Tests correction parsing, Neo4j persistence, and in-memory updates.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from praxis.graph.nodes import correction_node
from praxis.graph.state import AgentState
from praxis.models.schemas import (
    EmailTriage,
    InboundEmail,
    Intent,
    MemoryContext,
    Priority,
    Sentiment,
)


@pytest.fixture
def test_state() -> AgentState:
    """AgentState with a sample email and triage result."""
    return {
        "email_content": InboundEmail(
            message_id="m1",
            sender="correction@example.com",
            subject="Test Correction",
            body="Always CC legal on these threads.",
        ),
        "triage_result": EmailTriage(
            priority=Priority.NORMAL,
            intent=Intent.GENERAL_INQUIRY,
            sentiment=Sentiment.NEUTRAL,
            is_spam=False,
            sender_vip=False,
            confidence=0.5,
        ),
        "memory_context": MemoryContext(),
    }


def test_correction_node_parses_json_correction(test_state: AgentState) -> None:
    """correction_node parses JSON correction message and updates memory_context."""
    test_state["final_response"] = '{"category": "intent_correction", "correction": "should be urgent"}'
    result = correction_node(test_state)

    assert "memory_context" in result
    corrections = result["memory_context"].corrections
    assert len(corrections) == 1
    assert corrections[0].category == "intent_correction"
    assert corrections[0].confidence == 0.9


def test_correction_node_parses_text_correction(test_state: AgentState) -> None:
    """correction_node handles plain-text correction (not JSON)."""
    test_state["final_response"] = "This should be spam"
    result = correction_node(test_state)

    corrections = result["memory_context"].corrections
    assert len(corrections) == 1
    assert "spam" in corrections[0].rule


def test_correction_node_limits_to_fifty(test_state: AgentState) -> None:
    """correction_node keeps at most 50 corrections."""
    # Pre-populate with 55 corrections
    from praxis.models.schemas import CorrectionSummary
    test_state["memory_context"] = MemoryContext(
        corrections=[CorrectionSummary(correction_id=f"old_{i}", category="test", rule="x", confidence=0.5, applied_count=0) for i in range(55)]
    )
    test_state["final_response"] = "New correction"
    result = correction_node(test_state)

    corrections = result["memory_context"].corrections
    assert len(corrections) == 50  # Capped at 50


def test_correction_node_neo4j_failure_is_graceful(test_state: AgentState) -> None:
    """correction_node doesn't crash if Neo4j is unavailable."""
    test_state["final_response"] = "JSON correction"
    # Patch Neo4jClient to raise an exception
    with patch("praxis.services.neo4j_client.Neo4jContextClient", side_effect=Exception("connection failed")):
        # Should not raise — graceful degradation
        result = correction_node(test_state)
    # corrections should still be in memory
    corrections = result["memory_context"].corrections
    assert len(corrections) == 1


def test_correction_node_returns_updated_state(test_state: AgentState) -> None:
    """correction_node returns a dict with memory_context and final_response."""
    test_state["final_response"] = "New correction"
    result = correction_node(test_state)

    assert "memory_context" in result
    assert isinstance(result["memory_context"], MemoryContext)
    assert "final_response" in result
    assert result["final_response"].startswith("Correction recorded:")
