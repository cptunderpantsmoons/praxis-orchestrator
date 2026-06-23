"""Tests for the native ReAct tool registry.

Critical #2 from the v0.2.0 whole-branch review: ``_react_native`` only
bound 5 of the 13 spec-required tools. The other 8 were reachable via
``_execute_tool`` name-dispatch in legacy mode but not exposed as
``StructuredTool`` objects for ``bind_tools`` — so the native model had
no way to discover or invoke them.

These tests verify all 13 tools are bound in native mode.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from praxis.config import reset_settings
from praxis.graph.state import AgentState
from praxis.router import UmansConcurrencyRouter


# The 13 spec-required tools (Critical #2). update_sender_style is
# conditional on sender_style_enabled, so we test for 12 unconditionally
# and 13 when the feature flag is on.
SPEC_REQUIRED_TOOL_NAMES = {
    "reply_email",
    "send_email",
    "search_inbox",
    "list_threads",
    "hermes_recall",
    "hermes_store",
    "hermes_learn",
    "deep_research",
    "analyze_document",
    "create_report",
    "delegate_to_agent",
    "list_agents",
    "update_sender_style",
}


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


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


def _build_router_model_and_spy():
    """Build a mock router/model pair and a spy that captures bind_tools calls.

    Returns (router, model, captured_tools). The spy wraps the real
    ``bind_tools`` so the model still gets a functional bound model
    (needed for ``ainvoke`` to work in the loop).
    """
    final_response = _mock_final_response("FINAL:Done.")

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=final_response))
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter(http_client=client)

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)

    captured_tools: list[Any] = []
    original_bind_tools = model.bind_tools

    def spy_bind_tools(tools, **kwargs):
        captured_tools.extend(tools)
        return original_bind_tools(tools, **kwargs)

    # Bind the spy as an instance attribute (overrides the class method
    # for this instance only). We can't use ``patch.object`` because
    # ``UmansChatModel`` is a Pydantic model and ``patch.object`` tries
    # to ``delattr`` on exit, which Pydantic rejects.
    object.__setattr__(model, "bind_tools", spy_bind_tools)

    return router, model, captured_tools


async def _mock_reply_email(inbox_id, message_id, body, sent_message_ids=None, **kwargs):
    if sent_message_ids is not None:
        sent_message_ids.add(message_id)
    return "Reply sent successfully. Message ID: test"


@pytest.mark.asyncio
async def test_native_mode_binds_all_spec_tools(monkeypatch):
    """Verify ``_react_native`` binds all 13 spec-required tools.

    We spy on the model's ``bind_tools`` call to capture the tool list,
    then invoke ``_react_native`` and assert the captured list contains
    all 13 tool names.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    # Enable sender_style so update_sender_style is bound too.
    monkeypatch.setenv("SENDER_STYLE_ENABLED", "true")
    reset_settings()

    router, model, captured_tools = _build_router_model_and_spy()

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", side_effect=_mock_reply_email):
        from praxis.graph.nodes import _react_native
        await _react_native(_build_state())

    bound_names = {getattr(t, "name", None) for t in captured_tools}
    missing = SPEC_REQUIRED_TOOL_NAMES - bound_names
    assert not missing, (
        f"Missing {len(missing)} spec-required tools in native bind_tools: "
        f"{sorted(missing)}. Got: {sorted(bound_names)}"
    )


@pytest.mark.asyncio
async def test_native_mode_binds_12_tools_when_sender_style_disabled(monkeypatch):
    """When ``sender_style_enabled`` is False, 12 of the 13 tools are
    bound (``update_sender_style`` is omitted).
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    monkeypatch.setenv("SENDER_STYLE_ENABLED", "false")
    reset_settings()

    router, model, captured_tools = _build_router_model_and_spy()

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", side_effect=_mock_reply_email):
        from praxis.graph.nodes import _react_native
        await _react_native(_build_state())

    bound_names = {getattr(t, "name", None) for t in captured_tools}
    expected_without_sender_style = SPEC_REQUIRED_TOOL_NAMES - {"update_sender_style"}
    missing = expected_without_sender_style - bound_names
    assert not missing, (
        f"Missing {len(missing)} spec-required tools: {sorted(missing)}. "
        f"Got: {sorted(bound_names)}"
    )
    assert "update_sender_style" not in bound_names, (
        "update_sender_style should NOT be bound when sender_style_enabled=False"
    )


@pytest.mark.asyncio
async def test_native_agent_tools_have_pydantic_arg_schemas(monkeypatch):
    """The 8 agent tools (hermes_*, deep_research, analyze_document,
    create_report, delegate_to_agent, list_agents) must have Pydantic
    arg schemas so the Umans API can parse the model's tool_call
    arguments.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    reset_settings()

    router, model, captured_tools = _build_router_model_and_spy()

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", side_effect=_mock_reply_email):
        from praxis.graph.nodes import _react_native
        await _react_native(_build_state())

    agent_tool_names = {
        "hermes_recall",
        "hermes_store",
        "hermes_learn",
        "deep_research",
        "analyze_document",
        "create_report",
        "delegate_to_agent",
        "list_agents",
    }
    agent_tools = [t for t in captured_tools if t.name in agent_tool_names]
    assert len(agent_tools) == 8, (
        f"Expected 8 agent tools, got {len(agent_tools)}: "
        f"{[t.name for t in agent_tools]}"
    )

    from pydantic import BaseModel

    for tool in agent_tools:
        # StructuredTool exposes the Pydantic schema via .args_schema.
        assert tool.args_schema is not None, (
            f"Tool {tool.name} has no args_schema — the Umans API can't "
            f"parse its arguments."
        )
        # The schema must be a Pydantic BaseModel subclass.
        assert isinstance(tool.args_schema, type) and issubclass(
            tool.args_schema, BaseModel
        ), f"Tool {tool.name} args_schema is not a Pydantic BaseModel subclass"
