"""Tests for the Context Loading Node Neo4j integration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from praxis.graph.nodes import context_loading_node
from praxis.graph.state import AgentState
from praxis.models.schemas import (
    CorrectionSummary,
    EmailTriage,
    InboundEmail,
    Intent,
    MemoryContext,
    Priority,
    Sentiment,
    ThreadSummary,
)
from praxis.services.neo4j_client import Neo4jContextClient


@pytest.mark.asyncio
async def test_context_loading_populates_memory_context() -> None:
    """The node queries Neo4j and injects sender history + corrections."""
    client = AsyncMock(spec=Neo4jContextClient)
    client.fetch_sender_history = AsyncMock(
        return_value=[
            ThreadSummary(
                thread_id="t1",
                subject="Previous invoice",
                last_message_summary="Paid last invoice",
                timestamp=datetime.now(UTC),
            ),
        ]
    )
    client.fetch_relevant_corrections = AsyncMock(
        return_value=[
            CorrectionSummary(
                correction_id="c1",
                category="policy",
                rule="Always CC legal",
                confidence=0.92,
                applied_count=3,
            ),
        ]
    )

    triage = EmailTriage(
        priority=Priority.NORMAL,
        intent=Intent.INVOICE_PROCESSING,
        sentiment=Sentiment.NEUTRAL,
        is_spam=False,
        sender_vip=False,
        confidence=0.9,
    )
    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Invoice",
            body="Invoice question",
        ),
        "triage_result": triage,
    }

    result = await context_loading_node(
        state,
        config={"configurable": {"neo4j_client": client}},
    )

    memory = result["memory_context"]
    assert isinstance(memory, MemoryContext)
    assert len(memory.sender_history) == 1
    assert memory.sender_history[0].subject == "Previous invoice"
    assert len(memory.corrections) == 1
    assert memory.corrections[0].rule == "Always CC legal"
    client.fetch_relevant_corrections.assert_awaited_once_with(
        "user@example.com",
        intent_category="invoice_processing",
        limit=5,
    )


@pytest.mark.asyncio
async def test_context_loading_defaults_when_no_neo4j() -> None:
    """Without a Neo4j client the node returns empty context gracefully."""
    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1",
            sender="user@example.com",
            subject="Hello",
            body="Hello",
        ),
    }

    result = await context_loading_node(state, config=None)
    memory = result["memory_context"]
    assert isinstance(memory, MemoryContext)
    assert memory.sender_history == []
    assert memory.corrections == []
