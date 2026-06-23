# tests/test_email_tools_html.py
import asyncio
from unittest.mock import AsyncMock, patch

from praxis.tools.email_tools import _reply_email, _send_email


def test_send_email_forwards_body_html_to_agentmail_v2():
    with patch("praxis.tools.email_tools.get_agentmail_v2") as mock_get:
        client = mock_get.return_value
        client.send_message = AsyncMock(return_value=type("R", (), {"message_id": "msg_1"})())
        result = asyncio.run(_send_email(
            to="user@example.com", subject="S", body="text", body_html="<p>html</p>",
            message_id="m1", sent_message_ids=set(),
        ))
        assert "msg_1" in result
        _, kwargs = client.send_message.call_args
        assert kwargs["body_text"] == "text"
        assert kwargs["body_html"] == "<p>html</p>"

def test_reply_email_uses_legacy_client_when_html_provided():
    with patch("praxis.tools.email_tools.get_agentmail_client") as mock_get_legacy, \
         patch("praxis.tools.email_tools.get_agentmail_v2") as mock_get_v2:
        legacy_client = mock_get_legacy.return_value
        legacy_client.reply_to_message = AsyncMock(return_value={"message_id": "msg_r"})
        result = asyncio.run(_reply_email(
            inbox_id="ib_x", message_id="m1", body="text", body_html="<p>html</p>",
            sent_message_ids=set(),
        ))
        assert "msg_r" in result
        legacy_client.reply_to_message.assert_awaited_once()
        mock_get_v2.return_value.reply_to_message.assert_not_called()

def test_reply_email_uses_v2_client_when_no_html():
    with patch("praxis.tools.email_tools.get_agentmail_client") as mock_get_legacy, \
         patch("praxis.tools.email_tools.get_agentmail_v2") as mock_get_v2:
        v2_client = mock_get_v2.return_value
        v2_client.reply_to_message = AsyncMock(return_value=type("R", (), {"message_id": "msg_r"})())
        result = asyncio.run(_reply_email(
            inbox_id="ib_x", message_id="m1", body="text",
            sent_message_ids=set(),
        ))
        assert "msg_r" in result
        v2_client.reply_to_message.assert_awaited_once()
        mock_get_legacy.return_value.reply_to_message.assert_not_called()

def test_send_email_without_html_still_works():
    with patch("praxis.tools.email_tools.get_agentmail_v2") as mock_get:
        client = mock_get.return_value
        client.send_message = AsyncMock(return_value=type("R", (), {"message_id": "msg_1"})())
        asyncio.run(_send_email(
            to="u@e.com", subject="S", body="text", message_id="m1", sent_message_ids=set(),
        ))
        _, kwargs = client.send_message.call_args
        assert kwargs.get("body_html") is None
