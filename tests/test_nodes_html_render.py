"""Verify that all outbound send sites in ``nodes.py`` render an HTML body
and pass it as ``body_html=`` to ``_reply_email`` / ``_send_email``.

Covers the 5 send sites identified in Task 3 of the email-template plan:

- Site A: ``_react_native`` final-response path (model emits plain text,
  native loop sends the reply itself).
- Site B: ``_react_native`` safe-fallback path (max_iterations exhausted).
- Site C: ``_execute_tool_inner`` ``reply_email`` tool_call (also covers
  the legacy ``TOOL:reply_email(...)`` path, which routes through Site C).
- Site E: ``_react_legacy`` FINAL: reply fallback (``_reply_email``).
- Site F: ``_react_legacy`` FINAL: send fallback (``_send_email`` when
  ``_reply_email`` returns a non-"successfully" string).

Each test mocks the underlying send function in ``praxis.graph.nodes`` to
capture the ``body_html`` kwarg, drives the relevant code path, then asserts
the captured HTML contains the PRAXIS brand marker and the Tech gradient
stylesheet marker.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from praxis.config import reset_settings
from praxis.graph.state import AgentState
from praxis.models.schemas import AgentMetadata, InboundEmail
from praxis.router import UmansConcurrencyRouter


# ── Shared helpers ────────────────────────────────────────────────────────

def _mock_response(content: str):
    return {
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "model": "umans-flash",
    }


def _build_state(email_body: str = "Hello, please help me.") -> AgentState:
    return {
        "email_content": InboundEmail(
            message_id="msg_1",
            sender="jane.doe@example.com",
            recipients=["praxis@agentmail.to"],
            subject="Question about invoice",
            body=email_body,
            received_at="2026-06-22T00:00:00Z",
        ),
        "metadata": AgentMetadata(thread_id="t1"),
        "messages": [],
    }


def _build_router_model(content: str):
    """Build a (router, model) pair backed by an httpx MockTransport that
    returns the given content for every chat-completion request."""
    response = _mock_response(content)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=response))
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter(http_client=client)

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)
    return router, model


def _assert_branded_html(body_html: Any) -> None:
    """Shared assertion: the captured body_html is a non-empty string that
    contains the PRAXIS brand and the Tech gradient stylesheet marker."""
    assert body_html is not None, "body_html was not passed to the send function"
    assert isinstance(body_html, str), f"body_html must be str, got {type(body_html)}"
    assert "PRAXIS" in body_html, "body_html missing PRAXIS brand marker"
    assert "linear-gradient" in body_html, "body_html missing Tech gradient stylesheet"


# ── Site C: _execute_tool_inner reply_email tool_call ─────────────────────

@pytest.mark.asyncio
async def test_site_c_reply_email_tool_renders_html(monkeypatch):
    """Site C: ``_execute_tool_inner`` with a ``reply_email`` tool_call
    renders an HTML body from the tool's ``body`` arg and the inbound
    email's subject/sender, then forwards it as ``body_html=`` to
    ``_reply_email``.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    reset_settings()

    state = _build_state()
    tool_call = {
        "name": "reply_email",
        "inbox_id": "ib_test",
        "message_id": "msg_1",
        "body": "Thanks for your question — the invoice is paid.",
    }

    with patch("praxis.graph.nodes._reply_email", new=AsyncMock()) as mock_reply:
        mock_reply.return_value = "Reply sent successfully. Message ID: msg_r"
        from praxis.graph.nodes import _execute_tool_inner
        await _execute_tool_inner(
            dict(tool_call),
            sent_message_ids=set(),
            state=state,
        )

    mock_reply.assert_awaited_once()
    _, kwargs = mock_reply.call_args
    assert kwargs["body"] == "Thanks for your question — the invoice is paid."
    _assert_branded_html(kwargs.get("body_html"))


@pytest.mark.asyncio
async def test_site_c_send_email_tool_renders_html(monkeypatch):
    """Site C (send_email variant): ``_execute_tool_inner`` with a
    ``send_email`` tool_call renders an HTML body and forwards it as
    ``body_html=`` to ``_send_email``.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    reset_settings()

    state = _build_state()
    tool_call = {
        "name": "send_email",
        "to": "user@example.com",
        "subject": "Re: Question",
        "body": "Sent via send_email tool.",
        "message_id": "msg_1",
    }

    with patch("praxis.graph.nodes._send_email", new=AsyncMock()) as mock_send:
        mock_send.return_value = "Email sent successfully. Message ID: msg_s"
        from praxis.graph.nodes import _execute_tool_inner
        await _execute_tool_inner(
            dict(tool_call),
            sent_message_ids=set(),
            state=state,
        )

    mock_send.assert_awaited_once()
    _, kwargs = mock_send.call_args
    assert kwargs["body"] == "Sent via send_email tool."
    _assert_branded_html(kwargs.get("body_html"))


@pytest.mark.asyncio
async def test_site_c_skips_html_when_state_is_none(monkeypatch):
    """Site C guard: when ``state is None`` (no inbound email context),
    ``body_html`` must be ``None`` — we can't render HTML without a sender.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    reset_settings()

    tool_call = {
        "name": "reply_email",
        "inbox_id": "ib_test",
        "message_id": "msg_1",
        "body": "No state context.",
    }

    with patch("praxis.graph.nodes._reply_email", new=AsyncMock()) as mock_reply:
        mock_reply.return_value = "Reply sent successfully. Message ID: msg_r"
        from praxis.graph.nodes import _execute_tool_inner
        await _execute_tool_inner(
            dict(tool_call),
            sent_message_ids=set(),
            state=None,
        )

    _, kwargs = mock_reply.call_args
    assert kwargs.get("body_html") is None, (
        "body_html must be None when state is None (no sender context)"
    )


# ── Site A: _react_native final response ─────────────────────────────────

@pytest.mark.asyncio
async def test_site_a_native_final_response_renders_html(monkeypatch):
    """Site A: ``_react_native`` final-response path.

    The model emits a plain-text reply (no tool_calls) — the native loop
    sends it itself via ``_reply_email``. The captured ``body_html`` must
    be branded.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    reset_settings()

    # Plain reply, no TOOL: prefix → triggers the final-response send path.
    router, model = _build_router_model("Here is your invoice status: paid in full.")

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", new=AsyncMock()) as mock_reply:
        mock_reply.return_value = "Reply sent successfully. Message ID: msg_a"
        from praxis.graph.nodes import _react_native
        await _react_native(_build_state())

    assert mock_reply.await_count >= 1, "Site A should have called _reply_email"
    _, kwargs = mock_reply.call_args
    _assert_branded_html(kwargs.get("body_html"))


# ── Site B: _react_native safe fallback ───────────────────────────────────

@pytest.mark.asyncio
async def test_site_b_native_safe_fallback_renders_html(monkeypatch):
    """Site B: ``_react_native`` safe-fallback path.

    The model returns empty content (or pure reasoning) on every iteration,
    so ``max_iterations`` is exhausted and the ``SAFE_FALLBACK_REPLY`` is
    sent via ``_reply_email``. The captured ``body_html`` must be branded.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    reset_settings()

    # Empty content → never breaks the loop early → triggers safe fallback.
    router, model = _build_router_model("")

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", new=AsyncMock()) as mock_reply:
        mock_reply.return_value = "Reply sent successfully. Message ID: msg_b"
        from praxis.graph.nodes import _react_native
        await _react_native(_build_state(), max_iterations=1)

    assert mock_reply.await_count >= 1, "Site B should have called _reply_email for the fallback"
    _, kwargs = mock_reply.call_args
    _assert_branded_html(kwargs.get("body_html"))


# ── Site E: _react_legacy FINAL: reply fallback ──────────────────────────

@pytest.mark.asyncio
async def test_site_e_legacy_final_reply_fallback_renders_html(monkeypatch):
    """Site E: ``_react_legacy`` FINAL: reply fallback.

    The model emits a plain ``FINAL:`` reply (no TOOL: calls). The legacy
    path sends it via ``_reply_email``. The captured ``body_html`` must be
    branded.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "legacy")
    reset_settings()

    router, model = _build_router_model("FINAL:Hello from legacy mode.")

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", new=AsyncMock()) as mock_reply:
        mock_reply.return_value = "Reply sent successfully. Message ID: msg_e"
        from praxis.graph.nodes import react_node
        await react_node(_build_state())

    assert mock_reply.await_count >= 1, "Site E should have called _reply_email"
    _, kwargs = mock_reply.call_args
    _assert_branded_html(kwargs.get("body_html"))


# ── Site F: _react_legacy FINAL: send fallback ───────────────────────────

@pytest.mark.asyncio
async def test_site_f_legacy_final_send_fallback_renders_html(monkeypatch):
    """Site F: ``_react_legacy`` FINAL: send fallback.

    The model emits a plain ``FINAL:`` reply. ``_reply_email`` returns a
    non-"successfully" string, so the legacy path falls back to
    ``_send_email``. The captured ``body_html`` on ``_send_email`` must be
    branded.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "legacy")
    reset_settings()

    router, model = _build_router_model("FINAL:Fallback send test.")

    async def failing_reply(inbox_id, message_id, body, sent_message_ids=None, **_):
        # Partial-failure: returns non-"successfully" → triggers _send_email.
        return "Failed to send reply: simulated partial failure"

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", new=AsyncMock(side_effect=failing_reply)), \
         patch("praxis.graph.nodes._send_email", new=AsyncMock()) as mock_send:
        mock_send.return_value = "Email sent successfully. Message ID: msg_f"
        from praxis.graph.nodes import react_node
        await react_node(_build_state())

    assert mock_send.await_count >= 1, (
        "Site F should have called _send_email after _reply_email failed"
    )
    _, kwargs = mock_send.call_args
    _assert_branded_html(kwargs.get("body_html"))
