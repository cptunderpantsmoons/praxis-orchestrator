"""Integration tests for AgentMailClient.

These tests mock the httpx transport to verify the client correctly:
- Sends emails via AgentMail API
- Reads inbox messages
- Replies to messages
- Handles API errors

The agent has its OWN mailbox via AgentMail — it does NOT access the user's mailbox.
AgentMail provides dedicated inboxes per agent.
"""

from __future__ import annotations

import httpx
import pytest

from praxis.services.agentmail_client import AgentMailClient

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def mock_transport() -> httpx.MockTransport:
    """Mock httpx transport for AgentMail API calls."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/inboxes":
            if request.method == "POST":
                return httpx.Response(200, json={"id": "ib_test123", "address": "agent@agentmail.to"})
            return httpx.Response(200, json=[{"id": "ib_test123", "address": "agent@agentmail.to"}])
        elif request.url.path.startswith("/api/v1/inboxes/") and "/messages" in request.url.path:
            if request.method == "POST":
                return httpx.Response(200, json={"id": "msg_sent", "status": "sent"})
            return httpx.Response(200, json=[])
        elif request.url.path.startswith("/api/v1/messages/") and "/reply" in request.url.path:
            return httpx.Response(200, json={"id": "msg_reply", "status": "sent"})
        return httpx.Response(200, json={"status": "ok"})

    return httpx.MockTransport(handler)


@pytest.fixture
def client(mock_transport: httpx.MockTransport) -> AgentMailClient:
    """AgentMailClient with mocked transport."""
    return AgentMailClient(
        api_key="test-key",
        base_url="https://api.agentmail.to",
        transport=mock_transport,
    )


# ── Tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_initialization() -> None:
    """Client initializes with correct headers."""
    client = AgentMailClient(api_key="my-key", base_url="https://api.agentmail.to")
    assert client._client.headers["Authorization"] == "Bearer my-key"
    await client.close()


@pytest.mark.asyncio
async def test_create_inbox(client: AgentMailClient) -> None:
    """create_inbox calls AgentMail API and returns inbox data."""
    result = await client.create_inbox(username="test-agent")
    assert result["id"] == "ib_test123"
    assert result["address"] == "agent@agentmail.to"


@pytest.mark.asyncio
async def test_send_message(client: AgentMailClient) -> None:
    """send_message sends email through AgentMail API."""
    result = await client.send_message(
        inbox_id="ib_test123",
        to="user@example.com",
        subject="Test Reply",
        body="This is a reply from the agent.",
    )
    assert result["status"] == "sent"


@pytest.mark.asyncio
async def test_reply_to_message(client: AgentMailClient) -> None:
    """reply_to_message sends reply in existing thread."""
    result = await client.reply_to_message(
        message_id="msg_original",
        body="Thanks for your message!",
        reply_all=False,
    )
    assert result["status"] == "sent"


@pytest.mark.asyncio
async def test_list_inboxes(client: AgentMailClient) -> None:
    """list_inboxes returns list of agent's inboxes."""
    result = await client.list_inboxes()
    assert isinstance(result, list)
    assert len(result) >= 1
    assert result[0]["id"] == "ib_test123"


@pytest.mark.asyncio
async def test_close(client: AgentMailClient) -> None:
    """close() closes the underlying httpx client."""
    await client.close()
    # After close, further requests should fail
    # (This is implicit — just verify no exception on close)


@pytest.mark.asyncio
async def test_error_handling_401() -> None:
    """Client raises on 401 Unauthorized."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Unauthorized"})

    client = AgentMailClient(
        api_key="bad-key",
        base_url="https://api.agentmail.to",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(Exception, match="401"):
        await client.list_inboxes()
    await client.close()


@pytest.mark.asyncio
async def test_error_handling_500() -> None:
    """Client raises on 500 Internal Server Error."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal Server Error"})

    client = AgentMailClient(
        api_key="test-key",
        base_url="https://api.agentmail.to",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(Exception, match="500"):
        await client.list_inboxes()
    await client.close()
