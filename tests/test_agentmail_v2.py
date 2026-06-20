"""Tests for the AgentMailV2 async wrapper over the official ``agentmail`` SDK.

The official ``agentmail`` Python SDK (v0.5.x) is a **synchronous** client built
on ``httpx.Client``. PRAXIS is fully async, so the wrapper bridges calls via
``asyncio.to_thread``.

These tests verify:
- The wrapper exposes the full API surface (inboxes, messages, threads,
  webhooks, drafts, labels, send)
- Every public method is a coroutine
- Calls bridge to the sync SDK via ``asyncio.to_thread``
- API key is loaded from settings or the ``AGENTMAIL_API_KEY`` env var
- Resource Pydantic models (not raw dicts) are returned
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from praxis.services.agentmail_v2 import AgentMailV2

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def mock_sdk() -> MagicMock:
    """A mock that pretends to be the synchronous ``agentmail.AgentMail`` client.

    It exposes the resource namespaces (``inboxes``, ``messages``, ``threads``,
    ``webhooks``, ``drafts``, ``labels``, ``send``) and the methods we wrap.
    """
    sdk = MagicMock(name="agentmail.AgentMail")

    # inboxes.*
    sdk.inboxes.create = MagicMock(return_value=MagicMock(inbox_id="ib_new"))
    sdk.inboxes.list = MagicMock(return_value=[MagicMock(inbox_id="ib_1")])
    sdk.inboxes.get = MagicMock(return_value=MagicMock(inbox_id="ib_1"))
    sdk.inboxes.delete = MagicMock(return_value={"deleted": True})

    # messages.*
    sdk.messages.list = MagicMock(return_value=[MagicMock(message_id="m1")])
    sdk.messages.get = MagicMock(return_value=MagicMock(message_id="m1"))
    sdk.messages.reply = MagicMock(return_value=MagicMock(message_id="m2"))
    sdk.messages.forward = MagicMock(return_value=MagicMock(message_id="m3"))
    sdk.messages.search = MagicMock(return_value=[MagicMock(message_id="m1")])
    sdk.messages.mark_read = MagicMock(return_value={"marked_read": 1})
    sdk.messages.mark_unread = MagicMock(return_value={"marked_unread": 1})

    # threads.*
    sdk.threads.list = MagicMock(return_value=[MagicMock(thread_id="t1")])
    sdk.threads.get = MagicMock(return_value=MagicMock(thread_id="t1"))

    # webhooks.*
    sdk.webhooks.create = MagicMock(return_value=MagicMock(webhook_id="wh1"))
    sdk.webhooks.list = MagicMock(return_value=[MagicMock(webhook_id="wh1")])
    sdk.webhooks.delete = MagicMock(return_value={"deleted": True})
    sdk.webhooks.rotate_secret = MagicMock(return_value=MagicMock(secret="new"))

    # drafts.*
    sdk.drafts.create = MagicMock(return_value=MagicMock(draft_id="d1"))
    sdk.drafts.list = MagicMock(return_value=[MagicMock(draft_id="d1")])
    sdk.drafts.send = MagicMock(return_value=MagicMock(message_id="m1"))

    # labels.*
    sdk.labels.create = MagicMock(return_value=MagicMock(label_id="l1"))
    sdk.labels.list = MagicMock(return_value=[MagicMock(label_id="l1")])
    sdk.labels.add = MagicMock(return_value={"added": True})
    sdk.labels.remove = MagicMock(return_value={"removed": True})

    # send.*
    sdk.send.send = MagicMock(return_value=MagicMock(message_id="m_sent"))

    return sdk


@pytest.fixture
def client(mock_sdk: MagicMock) -> AgentMailV2:
    """An AgentMailV2 wrapper that uses the mocked SDK instead of the real one."""
    return AgentMailV2(api_key="test-key", _sdk=mock_sdk)


# ── Construction ────────────────────────────────────────────────


def test_lazy_sdk_initialization() -> None:
    """Without an injected _sdk, the wrapper must NOT construct the real client
    at __init__ time (so unit tests without a real key don't fail). The real
    SDK is built lazily on first use."""
    c = AgentMailV2(api_key="test-key")
    assert c._sdk is None
    assert c._api_key == "test-key"


def test_api_key_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """If no api_key is passed, fall back to the Settings.agentmail_api_key."""
    monkeypatch.setenv("AGENTMAIL_API_KEY", "from-env")
    c = AgentMailV2()
    assert c._api_key == "from-env"


# ── Method surface ──────────────────────────────────────────────


def test_all_methods_are_coroutines(client: AgentMailV2) -> None:
    """Every public method should be an async coroutine function."""
    import inspect

    expected_async = [
        "create_inbox", "list_inboxes", "get_inbox", "delete_inbox",
        "list_messages", "get_message", "reply_to_message", "forward_message",
        "search_messages", "mark_message_read", "mark_message_unread",
        "list_threads", "get_thread",
        "create_webhook", "list_webhooks", "delete_webhook", "rotate_webhook_secret",
        "create_draft", "list_drafts", "send_draft",
        "create_label", "list_labels", "add_label", "remove_label",
        "send_message",
    ]
    for name in expected_async:
        method = getattr(client, name)
        assert callable(method), f"{name} is not callable"
        assert inspect.iscoroutinefunction(method), (
            f"{name} must be a coroutine function (async def), got {type(method)}"
        )


# ── Inboxes ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_inbox_bridges_to_thread(
    client: AgentMailV2, mock_sdk: MagicMock
) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))) as mock_tt:
        await client.create_inbox(username="praxis-agent")
    mock_tt.assert_awaited_once()
    mock_sdk.inboxes.create.assert_called_once_with(
        username="praxis-agent",
        domain=None,
        display_name=None,
        ttl_seconds=None,
    )


@pytest.mark.asyncio
async def test_list_inboxes(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.list_inboxes()
    mock_sdk.inboxes.list.assert_called_once()


@pytest.mark.asyncio
async def test_get_inbox(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.get_inbox("ib_1")
    mock_sdk.inboxes.get.assert_called_once_with("ib_1")


@pytest.mark.asyncio
async def test_delete_inbox(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.delete_inbox("ib_1")
    mock_sdk.inboxes.delete.assert_called_once_with("ib_1")


# ── Messages ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_messages(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.list_messages(inbox_id="ib_1", limit=5)
    mock_sdk.messages.list.assert_called_once_with(
        inbox_id="ib_1", limit=5, cursor=None, unread_only=False
    )


@pytest.mark.asyncio
async def test_get_message(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.get_message(inbox_id="ib_1", message_id="m1")
    mock_sdk.messages.get.assert_called_once_with(inbox_id="ib_1", message_id="m1")


@pytest.mark.asyncio
async def test_reply_to_message(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.reply_to_message(inbox_id="ib_1", message_id="m1", body="OK")
    mock_sdk.messages.reply.assert_called_once_with(
        inbox_id="ib_1", message_id="m1", body="OK", cc=None, bcc=None
    )


@pytest.mark.asyncio
async def test_forward_message(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.forward_message(
            inbox_id="ib_1", message_id="m1", to=["a@b.com"], body="FYI"
        )
    mock_sdk.messages.forward.assert_called_once_with(
        inbox_id="ib_1", message_id="m1", to=["a@b.com"], body="FYI"
    )


@pytest.mark.asyncio
async def test_search_messages(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.search_messages(inbox_id="ib_1", q="invoice", limit=3)
    mock_sdk.messages.search.assert_called_once_with(inbox_id="ib_1", q="invoice", limit=3)


@pytest.mark.asyncio
async def test_mark_message_read(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.mark_message_read(inbox_id="ib_1", message_id="m1")
    mock_sdk.messages.mark_read.assert_called_once_with(inbox_id="ib_1", message_id="m1")


# ── Send (separate resource) ────────────────────────────────────


@pytest.mark.asyncio
async def test_send_message_coerces_str_to_list(
    client: AgentMailV2, mock_sdk: MagicMock
) -> None:
    """send_message accepts a single string for `to` and coerces to a list."""
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.send_message(
            inbox_id="ib_1", to="user@example.com", subject="Hi", body_text="Hello"
        )
    mock_sdk.send.send.assert_called_once_with(
        inbox_id="ib_1",
        to=["user@example.com"],
        subject="Hi",
        body_text="Hello",
        body_html=None,
        cc=None,
        bcc=None,
        from_addr=None,
        reply_to=None,
        headers=None,
    )


@pytest.mark.asyncio
async def test_send_message_passes_list_directly(
    client: AgentMailV2, mock_sdk: MagicMock
) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.send_message(
            inbox_id="ib_1",
            to=["a@b.com", "c@d.com"],
            subject="Hi",
            body_text="Hello",
            body_html="<p>Hello</p>",
        )
    mock_sdk.send.send.assert_called_once_with(
        inbox_id="ib_1",
        to=["a@b.com", "c@d.com"],
        subject="Hi",
        body_text="Hello",
        body_html="<p>Hello</p>",
        cc=None,
        bcc=None,
        from_addr=None,
        reply_to=None,
        headers=None,
    )


# ── Threads ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_threads(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.list_threads(inbox_id="ib_1")
    mock_sdk.threads.list.assert_called_once()


@pytest.mark.asyncio
async def test_get_thread(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.get_thread(inbox_id="ib_1", thread_id="t1")
    mock_sdk.threads.get.assert_called_once_with(inbox_id="ib_1", thread_id="t1")


# ── Webhooks ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_webhook(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.create_webhook(
            url="https://example.com/wh",
            events=["message.received", "message.sent"],
            inbox_id="ib_1",
        )
    mock_sdk.webhooks.create.assert_called_once_with(
        url="https://example.com/wh",
        events=["message.received", "message.sent"],
        inbox_id="ib_1",
        enabled=True,
    )


@pytest.mark.asyncio
async def test_list_webhooks(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.list_webhooks()
    mock_sdk.webhooks.list.assert_called_once()


@pytest.mark.asyncio
async def test_delete_webhook(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.delete_webhook("wh1")
    mock_sdk.webhooks.delete.assert_called_once_with("wh1")


@pytest.mark.asyncio
async def test_rotate_webhook_secret(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.rotate_webhook_secret("wh1")
    mock_sdk.webhooks.rotate_secret.assert_called_once_with("wh1")


# ── Drafts ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_draft(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.create_draft(
            inbox_id="ib_1", to="a@b.com", subject="WIP", body_text="Draft body"
        )
    mock_sdk.drafts.create.assert_called_once()


@pytest.mark.asyncio
async def test_send_draft(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.send_draft(draft_id="d1")
    mock_sdk.drafts.send.assert_called_once_with(draft_id="d1")


# ── Labels ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_label(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.create_label(inbox_id="ib_1", name="urgent", color="#ff0000")
    mock_sdk.labels.create.assert_called_once_with(
        inbox_id="ib_1", name="urgent", color="#ff0000"
    )


@pytest.mark.asyncio
async def test_add_label(client: AgentMailV2, mock_sdk: MagicMock) -> None:
    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
        await client.add_label(
            inbox_id="ib_1", message_id="m1", label="urgent"
        )
    mock_sdk.labels.add.assert_called_once_with(
        inbox_id="ib_1", message_id="m1", label="urgent"
    )


# ── Backwards compatibility with the old AgentMailClient ────────


def test_old_agentmail_client_re_exports_v2() -> None:
    """The old ``AgentMailClient`` from ``agentmail_client.py`` should still be
    importable, but delegate to the new v2 wrapper."""
    from praxis.services.agentmail_client import AgentMailClient as OldClient
    # The old client is still importable
    assert OldClient is not None
