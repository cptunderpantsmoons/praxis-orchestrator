"""Tests that sender style is loaded and injected into the system prompt."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from praxis.config import reset_settings
from praxis.features.sender_style import SenderStyle


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


@pytest.mark.asyncio
async def test_context_loading_fetches_sender_style(monkeypatch):
    """context_loading_node should fetch the sender's style and store it in metadata."""
    from praxis.models.schemas import AgentMetadata, InboundEmail
    from praxis.graph.state import AgentState

    state: AgentState = {
        "email_content": InboundEmail(
            message_id="m1", sender="user@example.com", recipients=["p@inbox"],
            subject="Test", body="Hello", received_at="2026-06-22T00:00:00Z",
        ),
        "metadata": AgentMetadata(),
        "messages": [],
    }

    style = SenderStyle(tone="casual", signature="Cheers, P")
    with patch("praxis.features.sender_style.get_style_for_sender", AsyncMock(return_value=style)), \
         patch("praxis.services.neo4j_client.Neo4jContextClient.fetch_sender_history", AsyncMock(return_value=[])), \
         patch("praxis.services.neo4j_client.Neo4jContextClient.fetch_relevant_corrections", AsyncMock(return_value=[])), \
         patch("praxis.services.hindsight_client.recall_memory", AsyncMock(return_value=[])):
        from praxis.graph.nodes import context_loading_node
        result = await context_loading_node(state)

    meta = result["metadata"]
    assert hasattr(meta, "sender_style")
    assert meta.sender_style == style


@pytest.mark.asyncio
async def test_build_prompt_includes_sender_style_block(monkeypatch):
    from praxis.models.schemas import AgentMetadata
    from praxis.graph.nodes import _build_praxis_v22_prompt

    style = SenderStyle(tone="casual", signature="Cheers, P", language="en")
    prompt = _build_praxis_v22_prompt(
        inbox_id="ib_default_agent_inbox",
        message_id="m1",
        sender="user@example.com",
        user_region="",
        corrections_text="",
        attachment_lines=[],
        sender_style=style,
    )
    assert "casual" in prompt.lower()
    assert "Cheers, P" in prompt
