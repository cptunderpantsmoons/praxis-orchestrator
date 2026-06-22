"""Tests for UmansChatModel.bind_tools (native tool-calling)."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from praxis.chat.wrappers import UmansChatModel
from praxis.config import reset_settings
from praxis.router import UmansConcurrencyRouter


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


def _mock_response_with_tool_call():
    """Return an OpenAI-compatible response dict with a tool_call."""
    return {
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_abc123",
                    "type": "function",
                    "function": {
                        "name": "reply_email",
                        "arguments": json.dumps({
                            "body": "Hello, thanks for your email.",
                        }),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "model": "umans-flash",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _build_router_with_mock(response_dict: dict[str, Any]) -> UmansConcurrencyRouter:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=response_dict)
    )
    client = httpx.AsyncClient(transport=transport)
    return UmansConcurrencyRouter(http_client=client)


@pytest.mark.asyncio
async def test_bind_tools_returns_new_instance():
    router = _build_router_with_mock(_mock_response_with_tool_call())
    model = UmansChatModel.create("umans-flash", router=router)
    bound = model.bind_tools([])
    assert bound is not model
    assert bound.model_name == model.model_name


@pytest.mark.asyncio
async def test_agenerate_with_tools_emits_tool_calls():
    router = _build_router_with_mock(_mock_response_with_tool_call())
    model = UmansChatModel.create("umans-flash", router=router)

    async def _noop(body: str) -> str:  # signature matches reply_email
        """Send a reply to an email."""
        return "ok"

    tool = StructuredTool.from_function(_noop, name="reply_email")
    bound = model.bind_tools([tool])

    result = await bound.ainvoke([HumanMessage(content="test")])
    assert result.tool_calls is not None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "reply_email"
    assert result.tool_calls[0]["args"] == {"body": "Hello, thanks for your email."}


@pytest.mark.asyncio
async def test_agenerate_without_tools_has_no_tool_calls():
    response = {
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Hello there."},
            "finish_reason": "stop",
        }],
        "model": "umans-flash",
    }
    router = _build_router_with_mock(response)
    model = UmansChatModel.create("umans-flash", router=router)
    result = await model.ainvoke([HumanMessage(content="hi")])
    assert result.tool_calls == []
    assert result.content == "Hello there."
