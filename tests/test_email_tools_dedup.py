"""Tests for the email dedup guard."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from praxis.tools.email_tools import _reply_email, _send_email


@pytest.mark.asyncio
async def test_reply_email_adds_message_id_to_sent_set(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "test")
    monkeypatch.setenv("AGENTMAIL_INBOX_ID", "ib_test")

    sent_ids: set[str] = set()
    mock_client = AsyncMock()
    mock_client.reply_to_message = AsyncMock(return_value={"id": "msg_123"})
    with patch("praxis.tools.email_tools.get_agentmail_v2", return_value=mock_client):
        result = await _reply_email(
            inbox_id="ib_test",
            message_id="msg_orig_1",
            body="Hello",
            sent_message_ids=sent_ids,
        )
    assert "msg_orig_1" in sent_ids
    assert "duplicate" not in result


@pytest.mark.asyncio
async def test_reply_email_skips_when_message_id_already_sent(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "test")
    monkeypatch.setenv("AGENTMAIL_INBOX_ID", "ib_test")

    sent_ids = {"msg_orig_1"}
    mock_client = AsyncMock()
    mock_client.reply_to_message = AsyncMock(return_value={"id": "msg_123"})
    with patch("praxis.tools.email_tools.get_agentmail_v2", return_value=mock_client):
        result = await _reply_email(
            inbox_id="ib_test",
            message_id="msg_orig_1",
            body="Hello",
            sent_message_ids=sent_ids,
        )
    assert "duplicate" in result.lower()
    mock_client.reply_to_message.assert_not_called()


@pytest.mark.asyncio
async def test_send_email_skips_when_message_id_already_in_set(monkeypatch):
    monkeypatch.setenv("AGENTMAIL_API_KEY", "test")
    monkeypatch.setenv("AGENTMAIL_INBOX_ID", "ib_test")

    sent_ids = {"msg_orig_2"}
    mock_client = AsyncMock()
    mock_client.send_message = AsyncMock(return_value={"id": "msg_new"})
    with patch("praxis.tools.email_tools.get_agentmail_v2", return_value=mock_client):
        result = await _send_email(
            to="user@example.com",
            subject="Re: Test",
            body="Hello",
            sent_message_ids=sent_ids,
            message_id="msg_orig_2",
        )
    assert "duplicate" in result.lower()
    mock_client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_reply_email_works_without_sent_set(monkeypatch):
    """Backwards-compat: sent_message_ids=None should still send."""
    monkeypatch.setenv("AGENTMAIL_API_KEY", "test")
    monkeypatch.setenv("AGENTMAIL_INBOX_ID", "ib_test")

    mock_client = AsyncMock()
    mock_client.reply_to_message = AsyncMock(return_value={"id": "msg_123"})
    with patch("praxis.tools.email_tools.get_agentmail_v2", return_value=mock_client):
        result = await _reply_email(
            inbox_id="ib_test",
            message_id="msg_orig_3",
            body="Hello",
        )
    assert "duplicate" not in result
