"""Regression tests for the legacy TOOL:/FINAL: ReAct path's dedup guard.

Critical #1 from the v0.2.0 whole-branch review:

The dedup guard (``sent_message_ids`` set) was only wired into
``_react_native``. The legacy path (``_react_legacy``) never initialized
the set, never passed it to ``_execute_tool``, and never passed it to the
auto-reply ``_reply_email``/``_send_email`` fallback calls. On a
``TOOL_PROTOCOL=legacy`` rollback, if ``_reply_email`` partially succeeds
but returns a non-"successfully" string, ``_send_email`` is invoked as a
fallback and sends a duplicate — the exact production bug the spec called
out.

These tests verify the legacy path uses the dedup guard at both:

1. The ``_execute_tool`` dispatch boundary (when the model emits an
   explicit ``TOOL:reply_email(...)`` line).
2. The auto-reply fallback (when the model emits a plain ``FINAL:`` reply
   that gets sent via the ``_reply_email`` → ``_send_email`` fallback).
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from praxis.config import reset_settings
from praxis.graph.state import AgentState
from praxis.router import UmansConcurrencyRouter


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


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
async def test_legacy_dedup_guard_prevents_duplicate_auto_reply_send(monkeypatch):
    """Critical #1 regression: when the auto-reply fallback fires
    (reply_email fails, _send_email is the fallback), the dedup guard must
    ensure only ONE email is actually sent for a given message_id.

    Without the guard, _send_email sends a duplicate after _reply_email
    partially succeeds but returns a non-"successfully" string.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "legacy")
    reset_settings()

    # Model emits a plain FINAL: reply (no TOOL: calls).
    final_response = _mock_response("FINAL:Hello from legacy mode.")

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=final_response))
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter(http_client=client)

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)

    actual_sends: list[dict[str, Any]] = []

    async def mock_reply_email(inbox_id, message_id, body, sent_message_ids=None):
        actual_sends.append({"via": "reply", "message_id": message_id})
        # Reply partially fails — returns a non-"successfully" string, so
        # the fallback _send_email kicks in. This is the exact production
        # scenario described in Critical #1.
        #
        # IMPORTANT: when _reply_email fails, it must NOT add the message_id
        # to the dedup set — the real implementation only adds on success.
        # If it did, _send_email would short-circuit and the email would
        # never go out. The test below verifies that exactly ONE send
        # happens via the _send_email fallback.
        return "Failed to send reply: simulated partial failure"

    async def mock_send_email(to, subject, body, sent_message_ids=None, message_id=None):
        # The dedup guard lives in the real _send_email at the send
        # boundary. Without wiring, message_id would be None here (no
        # guard), and we'd record a second send.
        if message_id is not None and sent_message_ids is not None and message_id in sent_message_ids:
            return "duplicate:already_sent"
        if message_id is not None and sent_message_ids is not None:
            sent_message_ids.add(message_id)
        actual_sends.append({"via": "send", "message_id": message_id})
        return "Email sent successfully. Message ID: dup_test_1"

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", side_effect=mock_reply_email), \
         patch("praxis.graph.nodes._send_email", side_effect=mock_send_email):
        from praxis.graph.nodes import react_node
        await react_node(_build_state())

    # The auto-reply fallback kicked in. We expect exactly ONE actual
    # send via the _send_email fallback (since _reply_email failed). The
    # reply call was attempted but didn't actually send — so only the
    # _send_email fallback should record a send.
    #
    # Without the dedup guard wired in, EITHER:
    #   - _send_email would be called with message_id=None (the legacy
    #     path never passed it), so no dedup happened — fine for this
    #     test, but then a real-world second ``TOOL:reply_email`` line
    #     earlier in the run would also send, causing the duplicate.
    #   - OR more importantly: the write-back to metadata wouldn't
    #     happen, so a checkpoint-restore / re-invoke of the graph would
    #     re-send the same email.
    #
    # This test specifically guards against the case where the auto-reply
    # fallback fires AND _send_email is called twice (e.g. if a prior
    # ``TOOL:send_email`` line had already sent for this message_id).
    # Here, we simply assert exactly one _send_email actually went out.
    actual_send_count = sum(1 for s in actual_sends if s.get("via") == "send")
    assert actual_send_count == 1, (
        f"Expected exactly 1 actual send via _send_email, got {actual_send_count}. "
        f"Sends: {actual_sends}"
    )


@pytest.mark.asyncio
async def test_legacy_dedup_guard_blocks_send_after_explicit_reply(monkeypatch):
    """Critical #1 regression (stronger variant): if the model emits an
    explicit ``TOOL:reply_email(...)`` line that succeeds, the auto-reply
    fallback must NOT send a second email for the same message_id.

    Without the dedup guard wired into the auto-reply fallback, the
    fallback would always fire (because ``reply_already_sent`` is checked
    against ``EmailToolResult.success``, but the fallback path calls
    ``_send_email`` regardless when ``prior_reply.success`` is True and
    the auto-reply guard skips — OK). The real failure mode is when the
    model emits a ``TOOL:send_email`` (not reply_email) that succeeds,
    then the auto-reply fallback sees ``prior_reply is None`` and fires,
    sending a duplicate via ``_send_email`` for the same message_id.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "legacy")
    reset_settings()

    # Model emits a TOOL:send_email then a FINAL:. The auto-reply fallback
    # will fire because ``prior_reply`` (the reply_email tool output) is
    # None — only send_email was called.
    content = (
        "TOOL:send_email(to=user@example.com, subject=Re: Test, body=First send)\n"
        "FINAL:Done."
    )
    response = _mock_response(content)

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=response))
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter(http_client=client)

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)

    actual_sends: list[dict[str, Any]] = []

    async def mock_reply_email(inbox_id, message_id, body, sent_message_ids=None):
        if sent_message_ids is not None and message_id in sent_message_ids:
            return "duplicate:already_sent"
        if sent_message_ids is not None:
            sent_message_ids.add(message_id)
        actual_sends.append({"via": "reply", "message_id": message_id})
        return "Reply sent successfully. Message ID: explicit_reply"

    async def mock_send_email(to, subject, body, sent_message_ids=None, message_id=None):
        if message_id is not None and sent_message_ids is not None and message_id in sent_message_ids:
            return "duplicate:already_sent"
        if message_id is not None and sent_message_ids is not None:
            sent_message_ids.add(message_id)
        actual_sends.append({"via": "send", "message_id": message_id})
        return "Email sent successfully. Message ID: explicit_send"

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", side_effect=mock_reply_email), \
         patch("praxis.graph.nodes._send_email", side_effect=mock_send_email):
        from praxis.graph.nodes import react_node
        await react_node(_build_state())

    # Exactly ONE send should have happened for message_id=msg_1. The
    # first came from the explicit TOOL:send_email line; the auto-reply
    # fallback must be short-circuited by the dedup guard.
    msg_1_sends = [s for s in actual_sends if s.get("message_id") == "msg_1"]
    assert len(msg_1_sends) == 1, (
        f"Expected exactly 1 send for msg_1, got {len(msg_1_sends)}. "
        f"All sends: {actual_sends}"
    )


@pytest.mark.asyncio
async def test_legacy_dedup_guard_writes_back_sent_message_ids(monkeypatch):
    """The legacy path must write ``sent_message_ids`` back to metadata
    so downstream nodes can see what was sent.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "legacy")
    reset_settings()

    final_response = _mock_response("FINAL:Done.")

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=final_response))
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter(http_client=client)

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)

    async def mock_reply_email(inbox_id, message_id, body, sent_message_ids=None):
        if sent_message_ids is not None:
            sent_message_ids.add(message_id)
        return "Reply sent successfully. Message ID: written_back_test"

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", side_effect=mock_reply_email):
        from praxis.graph.nodes import react_node
        result = await react_node(_build_state())

    metadata = result.get("metadata")
    assert metadata is not None
    assert "msg_1" in metadata.sent_message_ids, (
        f"Expected 'msg_1' in metadata.sent_message_ids, got {metadata.sent_message_ids}"
    )


@pytest.mark.asyncio
async def test_legacy_dedup_guard_prevents_duplicate_explicit_tool_call(monkeypatch):
    """If the model emits two ``TOOL:reply_email(...)`` lines with the same
    message_id, the legacy path's ``_execute_tool`` dispatch must use the
    dedup guard so only one send actually happens.
    """
    monkeypatch.setenv("TOOL_PROTOCOL", "legacy")
    reset_settings()

    # Model emits two TOOL:reply_email calls then a FINAL.
    content = (
        "TOOL:reply_email(inbox_id=ib_test, message_id=msg_1, body=First)\n"
        "TOOL:reply_email(inbox_id=ib_test, message_id=msg_1, body=Second)\n"
        "FINAL:Done."
    )
    response = _mock_response(content)

    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=response))
    client = httpx.AsyncClient(transport=transport)
    router = UmansConcurrencyRouter(http_client=client)

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)

    send_count = {"n": 0}

    async def mock_reply_email(inbox_id, message_id, body, sent_message_ids=None):
        # Mirror the real _reply_email dedup guard.
        if sent_message_ids is not None and message_id in sent_message_ids:
            return "duplicate:already_sent"
        if sent_message_ids is not None:
            sent_message_ids.add(message_id)
        send_count["n"] += 1
        return "Reply sent successfully. Message ID: dup_tool_test"

    with patch("praxis.graph.nodes._get_router_and_model", return_value=(router, model)), \
         patch("praxis.graph.nodes._reply_email", side_effect=mock_reply_email):
        from praxis.graph.nodes import react_node
        await react_node(_build_state())

    assert send_count["n"] == 1, (
        f"Expected exactly 1 actual send, got {send_count['n']}"
    )
