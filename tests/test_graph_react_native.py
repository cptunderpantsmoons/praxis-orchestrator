"""Tests for the refactored react_node (native tool-calling)."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from praxis.config import reset_settings
from praxis.graph.state import AgentState
from praxis.router import UmansConcurrencyRouter


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


def _mock_tool_call_response(tool_name: str, args: dict[str, Any]):
    return {
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(args),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "model": "umans-flash",
    }


def _mock_final_response(content: str):
    return {
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "model": "umans-flash",
    }


def _build_state(email_body: str = "Hello, please help me.") -> AgentState:
    from praxis.models.schemas import AgentMetadata, InboundEmail
    return {
        "email_content": InboundEmail(
            message_id="msg_1",
            sender="user@example.com",
            recipient="praxis@inbox.example",
            subject="Test",
            body=email_body,
            received_at="2026-06-22T00:00:00Z",
        ),
        "metadata": AgentMetadata(),
        "messages": [],
    }


@pytest.mark.asyncio
async def test_native_mode_executes_tool_call_then_returns_final(monkeypatch):
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    reset_settings()

    # First call returns a tool_call; second call returns final content.
    responses = [
        _mock_tool_call_response("dummy_search", {"query": "test"}),
        _mock_final_response("Here is your answer."),
    ]

    def handler(request):
        return httpx.Response(200, json=responses.pop(0))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter(http_client=client)

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)
    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)):
        from praxis.graph.nodes import react_node
        result = await react_node(_build_state())

    assert "final_response" in result
    assert "Here is your answer." in result["final_response"]


@pytest.mark.asyncio
async def test_native_mode_never_returns_raw_reasoning(monkeypatch):
    """If the model emits reasoning text but no tool_call and no FINAL, the
    safe fallback must be sent — not the raw reasoning."""
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    reset_settings()

    reasoning_response = {
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Let me analyze this email. The user is asking..."},
            "finish_reason": "stop",
        }],
        "model": "umans-flash",
    }

    # After the reasoning response, the loop iterates again. After max_iterations
    # it should send the safe fallback. We return the same reasoning 3 times.
    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        return httpx.Response(200, json=reasoning_response)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter(http_client=client)

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)

    sent_bodies: list[str] = []

    async def mock_reply(inbox_id, message_id, body, sent_message_ids=None):
        sent_bodies.append(body)
        return "sent successfully"

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", side_effect=mock_reply):
        from praxis.graph.nodes import react_node
        result = await react_node(_build_state())

    # The safe fallback must have been sent — NOT the raw reasoning.
    assert any("reviewing your email" in b.lower() for b in sent_bodies), \
        f"Expected safe fallback, got: {sent_bodies}"
    assert not any("Let me analyze" in b for b in sent_bodies), \
        f"Raw reasoning was sent: {sent_bodies}"


@pytest.mark.asyncio
async def test_native_mode_dedup_guard_prevents_duplicate_send(monkeypatch):
    """If reply_email is called twice for the same message_id, the second call is skipped."""
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    reset_settings()

    # First call: tool_call for reply_email. Second call: another tool_call for reply_email with same message_id.
    # Third call: final content.
    responses = [
        _mock_tool_call_response("reply_email", {"body": "First reply"}),
        _mock_tool_call_response("reply_email", {"body": "Second reply"}),
        _mock_final_response("Done."),
    ]

    def handler(request):
        if responses:
            return httpx.Response(200, json=responses.pop(0))
        return httpx.Response(200, json=_mock_final_response("Done."))

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter(http_client=client)

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)

    send_count = {"n": 0}

    async def mock_reply(inbox_id, message_id, body, sent_message_ids=None):
        send_count["n"] += 1
        if sent_message_ids is not None:
            if message_id in sent_message_ids:
                return "duplicate:already_sent"
            sent_message_ids.add(message_id)
        return "sent successfully"

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", side_effect=mock_reply):
        from praxis.graph.nodes import react_node
        await react_node(_build_state())

    assert send_count["n"] == 1, f"Expected 1 actual send, got {send_count['n']}"


@pytest.mark.asyncio
async def test_legacy_mode_still_uses_tool_final_protocol(monkeypatch):
    monkeypatch.setenv("TOOL_PROTOCOL", "legacy")
    reset_settings()

    legacy_response = {
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "FINAL:Hello from legacy mode."},
            "finish_reason": "stop",
        }],
        "model": "umans-flash",
    }

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=legacy_response))
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter(http_client=client)

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)):
        from praxis.graph.nodes import react_node
        result = await react_node(_build_state())

    assert "Hello from legacy mode." in result.get("final_response", "")
