"""Phase A integration: webhook → graph → reply with native tool-calling.

End-to-end smoke test that validates the email reply bug is fixed. A signed
AgentMail webhook is delivered to the FastAPI app, the Umans API is mocked
to return a ``reply_email`` tool_call, and the test verifies exactly ONE clean
reply is sent — no raw reasoning, no duplicates.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from praxis.config import reset_settings


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


def _triage_response() -> dict[str, Any]:
    """A valid EmailTriage JSON payload for the triage node."""
    return {
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "priority": "normal",
                    "intent": "general_inquiry",
                    "sentiment": "neutral",
                    "is_spam": False,
                    "sender_vip": False,
                    "confidence": 0.9,
                }),
            },
            "finish_reason": "stop",
        }],
        "model": "umans-flash",
    }


def _tool_call_response(body: str) -> dict[str, Any]:
    """A chat-completion response that emits a reply_email tool_call."""
    return {
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "reply_email",
                        "arguments": json.dumps({"body": body}),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "model": "umans-flash",
    }


def _final_response(content: str) -> dict[str, Any]:
    """A terminal chat-completion response (no tool_calls)."""
    return {
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "model": "umans-flash",
    }


def test_inbound_email_triggers_correct_reply(
    client: TestClient, svix_signer, monkeypatch
):
    """End-to-end: a webhook delivers an email, the graph runs, and exactly
    one clean reply is sent — no raw reasoning, no duplicates."""
    # Native tool-calling is the Phase A default.
    monkeypatch.setenv("TOOL_PROTOCOL", "native")
    monkeypatch.setenv("AGENTMAIL_INBOX_ID", "ib_test")
    reset_settings()

    reply_body = "Thanks for your email. Here is the answer."

    # Mock Umans API response sequence:
    #   1. triage node → valid EmailTriage JSON
    #   2. react iteration 1 → reply_email tool_call
    #   3. react iteration 2 → final content (loop exits)
    umans_responses: list[dict[str, Any]] = [
        _triage_response(),
        _tool_call_response(reply_body),
        _final_response("Reply delivered."),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if umans_responses:
            return httpx.Response(200, json=umans_responses.pop(0))
        return httpx.Response(200, json=_final_response("Reply delivered."))

    transport = httpx.MockTransport(handler)
    mock_http_client = httpx.AsyncClient(transport=transport)

    # Build a router that uses the mock transport so no real HTTP calls happen.
    from praxis.router import UmansConcurrencyRouter
    mock_router = UmansConcurrencyRouter(http_client=mock_http_client)

    from praxis.chat.wrappers import UmansChatModel
    mock_model = UmansChatModel.create("umans-flash", router=mock_router)

    # Record every reply_email send so we can assert exactly one clean reply.
    sent_replies: list[dict[str, Any]] = []

    async def mock_reply_to_message(
        inbox_id: str, message_id: str, body: str, **kwargs: Any
    ) -> str:
        sent_replies.append(
            {"inbox_id": inbox_id, "message_id": message_id, "body": body}
        )
        return "sent successfully"

    mock_agentmail = AsyncMock()
    mock_agentmail.reply_to_message = mock_reply_to_message

    with patch(
        "praxis.graph.nodes._get_router_and_model",
        return_value=(mock_router, mock_model),
    ), patch(
        "praxis.tools.email_tools.get_agentmail_v2",
        return_value=mock_agentmail,
    ):
        # AgentMail event-shape payload (see parse_agentmail_event).
        webhook_payload = {
            "type": "event",
            "event_type": "message.received",
            "event_id": "evt_integration_1",
            "message": {
                "id": "msg_inbound_1",
                "from": "user@example.com",
                "to": [{"address": "praxis@inbox.example"}],
                "subject": "Hello",
                "text": "Please help me with my question.",
            },
            "thread": {"thread_id": "thr_inbound_1"},
        }
        body = json.dumps(webhook_payload).encode()
        headers = svix_signer(body)

        resp = client.post("/webhook/email", content=body, headers=headers)

    assert resp.status_code == 200, resp.text
    assert len(sent_replies) == 1, (
        f"Expected exactly 1 reply, got {len(sent_replies)}: {sent_replies}"
    )

    sent = sent_replies[0]
    assert sent["inbox_id"] == "ib_test"
    assert sent["message_id"] == "msg_inbound_1"
    assert "Thanks for your email" in sent["body"], sent["body"]
    # No raw reasoning prefixes leaked into the reply.
    assert "Let me" not in sent["body"], sent["body"]
    assert "The user" not in sent["body"], sent["body"]
