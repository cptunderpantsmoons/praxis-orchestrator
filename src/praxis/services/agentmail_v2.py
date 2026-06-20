"""Async wrapper over the official ``agentmail`` Python SDK.

The official ``agentmail`` SDK (v0.5.x) is a **synchronous** client built on
``httpx.Client``. PRAXIS is fully async (asyncio + httpx.AsyncClient) so this
module bridges every SDK call through ``asyncio.to_thread`` to avoid blocking
the event loop.

This is the v2 client. The original ``agentmail_client.py`` shim still works
for backwards compatibility — it re-exports ``AgentMailClient`` as an alias
for ``AgentMailV2`` (in v0.1.0+ a deprecation notice is logged on import).

Method surface mirrors the SDK resource namespaces:

- ``inboxes``  — ``create_inbox``, ``list_inboxes``, ``get_inbox``, ``delete_inbox``
- ``messages`` — ``list_messages``, ``get_message``, ``reply_to_message``,
                 ``forward_message``, ``search_messages``,
                 ``mark_message_read``, ``mark_message_unread``
- ``threads``  — ``list_threads``, ``get_thread``
- ``webhooks`` — ``create_webhook``, ``list_webhooks``, ``delete_webhook``,
                 ``rotate_webhook_secret``
- ``drafts``   — ``create_draft``, ``list_drafts``, ``send_draft``
- ``labels``   — ``create_label``, ``list_labels``, ``add_label``, ``remove_label``
- ``send``     — ``send_message``

See the SDK source for parameter types — the wrapper follows the same names
and adds small conveniences (``to`` accepting a single string OR list).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

logger = structlog.get_logger()


def _default_api_key() -> str:
    """Resolve the AgentMail API key from settings or environment.

    Order:
    1. ``AGENTMAIL_API_KEY`` environment variable (SDK convention)
    2. ``Settings.agentmail_api_key`` (Pydantic settings via .env)
    """
    env_key = os.environ.get("AGENTMAIL_API_KEY", "")
    if env_key:
        return env_key
    # Lazy import to avoid hard dep at module load time
    from praxis.config import get_settings

    return get_settings().agentmail_api_key


class AgentMailV2:
    """Async wrapper over the synchronous ``agentmail.AgentMail`` SDK.

    The underlying sync SDK is created lazily on first use so the wrapper is
    safe to instantiate in tests without a real API key.

    Args:
        api_key: AgentMail API key. Falls back to ``AGENTMAIL_API_KEY`` env
                 var or ``Settings.agentmail_api_key``.
        base_url: AgentMail API base URL (default: https://api.agentmail.to).
        _sdk:     Inject a mock SDK for tests. Do not pass in production.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        _sdk: Any = None,
    ) -> None:
        self._api_key = api_key or _default_api_key()
        self._base_url = base_url or "https://api.agentmail.to"
        self._sdk = _sdk  # Lazy: real SDK is created on first call if None

    # ── Lazy SDK accessor ────────────────────────────────────────

    def _get_sdk(self) -> Any:
        if self._sdk is None:
            import agentmail

            self._sdk = agentmail.AgentMail(api_key=self._api_key, base_url=self._base_url)
        return self._sdk

    # ── Inboxes ──────────────────────────────────────────────────

    async def create_inbox(
        self,
        username: str,
        domain: str | None = None,
        display_name: str | None = None,
        ttl_seconds: int | None = None,
    ) -> Any:
        """Create a new inbox. Returns an ``Inbox`` model."""
        sdk = self._get_sdk()
        logger.info("agentmail.create_inbox", username=username)
        return await asyncio.to_thread(
            sdk.inboxes.create,
            username=username,
            domain=domain,
            display_name=display_name,
            ttl_seconds=ttl_seconds,
        )

    async def list_inboxes(self, limit: int = 50) -> list[Any]:
        sdk = self._get_sdk()
        logger.info("agentmail.list_inboxes")
        return await asyncio.to_thread(sdk.inboxes.list, limit=limit)

    async def get_inbox(self, inbox_id: str) -> Any:
        sdk = self._get_sdk()
        return await asyncio.to_thread(sdk.inboxes.get, inbox_id)

    async def delete_inbox(self, inbox_id: str) -> Any:
        sdk = self._get_sdk()
        logger.info("agentmail.delete_inbox", inbox_id=inbox_id)
        return await asyncio.to_thread(sdk.inboxes.delete, inbox_id)

    # ── Messages ─────────────────────────────────────────────────

    async def list_messages(
        self,
        inbox_id: str,
        limit: int = 50,
        cursor: str | None = None,
        unread_only: bool = False,
    ) -> tuple[list[Any], str | None]:
        sdk = self._get_sdk()
        return await asyncio.to_thread(
            sdk.messages.list,
            inbox_id=inbox_id,
            limit=limit,
            cursor=cursor,
            unread_only=unread_only,
        )

    async def get_message(self, inbox_id: str, message_id: str) -> Any:
        sdk = self._get_sdk()
        return await asyncio.to_thread(
            sdk.messages.get, inbox_id=inbox_id, message_id=message_id
        )

    async def reply_to_message(
        self,
        inbox_id: str,
        message_id: str,
        body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> Any:
        sdk = self._get_sdk()
        logger.info("agentmail.reply", inbox_id=inbox_id, message_id=message_id)
        return await asyncio.to_thread(
            sdk.messages.reply,
            inbox_id=inbox_id,
            message_id=message_id,
            body=body,
            cc=cc,
            bcc=bcc,
        )

    async def forward_message(
        self,
        inbox_id: str,
        message_id: str,
        to: list[str],
        body: str | None = None,
    ) -> Any:
        sdk = self._get_sdk()
        logger.info("agentmail.forward", inbox_id=inbox_id, message_id=message_id)
        return await asyncio.to_thread(
            sdk.messages.forward,
            inbox_id=inbox_id,
            message_id=message_id,
            to=to,
            body=body,
        )

    async def search_messages(
        self, inbox_id: str, q: str, limit: int = 20
    ) -> list[Any]:
        sdk = self._get_sdk()
        return await asyncio.to_thread(
            sdk.messages.search, inbox_id=inbox_id, q=q, limit=limit
        )

    async def mark_message_read(self, inbox_id: str, message_id: str) -> Any:
        sdk = self._get_sdk()
        return await asyncio.to_thread(
            sdk.messages.mark_read, inbox_id=inbox_id, message_id=message_id
        )

    async def mark_message_unread(self, inbox_id: str, message_id: str) -> Any:
        sdk = self._get_sdk()
        return await asyncio.to_thread(
            sdk.messages.mark_unread, inbox_id=inbox_id, message_id=message_id
        )

    # ── Send (new outbound resource, separate from messages.*) ────

    async def send_message(
        self,
        inbox_id: str,
        to: str | list[str],
        subject: str,
        body_text: str | None = None,
        body_html: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        from_addr: str | None = None,
        reply_to: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a new email. ``to`` may be a single address or a list."""
        sdk = self._get_sdk()
        to_list = [to] if isinstance(to, str) else to
        logger.info("agentmail.send", inbox_id=inbox_id, to=to_list, subject=subject)
        return await asyncio.to_thread(
            sdk.send.send,
            inbox_id=inbox_id,
            to=to_list,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            cc=cc,
            bcc=bcc,
            from_addr=from_addr,
            reply_to=reply_to,
            headers=headers,
        )

    # ── Threads ──────────────────────────────────────────────────

    async def list_threads(
        self,
        inbox_id: str,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[Any]:
        sdk = self._get_sdk()
        return await asyncio.to_thread(
            sdk.threads.list, inbox_id=inbox_id, limit=limit, cursor=cursor
        )

    async def get_thread(self, inbox_id: str, thread_id: str) -> Any:
        sdk = self._get_sdk()
        return await asyncio.to_thread(
            sdk.threads.get, inbox_id=inbox_id, thread_id=thread_id
        )

    # ── Webhooks ─────────────────────────────────────────────────

    async def create_webhook(
        self,
        url: str,
        events: list[str],
        inbox_id: str | None = None,
        enabled: bool = True,
    ) -> Any:
        """Register a webhook. ``events`` is a list of event names
        (e.g. ``['message.received', 'message.sent']``)."""
        sdk = self._get_sdk()
        logger.info("agentmail.create_webhook", url=url, events=events)
        return await asyncio.to_thread(
            sdk.webhooks.create,
            url=url,
            events=events,
            inbox_id=inbox_id,
            enabled=enabled,
        )

    async def list_webhooks(self) -> list[Any]:
        sdk = self._get_sdk()
        return await asyncio.to_thread(sdk.webhooks.list)

    async def delete_webhook(self, webhook_id: str) -> Any:
        sdk = self._get_sdk()
        logger.info("agentmail.delete_webhook", webhook_id=webhook_id)
        return await asyncio.to_thread(sdk.webhooks.delete, webhook_id)

    async def rotate_webhook_secret(self, webhook_id: str) -> Any:
        sdk = self._get_sdk()
        logger.info("agentmail.rotate_webhook_secret", webhook_id=webhook_id)
        return await asyncio.to_thread(sdk.webhooks.rotate_secret, webhook_id)

    # ── Drafts ───────────────────────────────────────────────────

    async def create_draft(
        self,
        inbox_id: str,
        to: str | list[str],
        subject: str,
        body_text: str | None = None,
        body_html: str | None = None,
    ) -> Any:
        sdk = self._get_sdk()
        to_list = [to] if isinstance(to, str) else to
        return await asyncio.to_thread(
            sdk.drafts.create,
            inbox_id=inbox_id,
            to=to_list,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
        )

    async def list_drafts(self, inbox_id: str) -> list[Any]:
        sdk = self._get_sdk()
        return await asyncio.to_thread(sdk.drafts.list, inbox_id=inbox_id)

    async def send_draft(self, draft_id: str) -> Any:
        sdk = self._get_sdk()
        logger.info("agentmail.send_draft", draft_id=draft_id)
        return await asyncio.to_thread(sdk.drafts.send, draft_id=draft_id)

    # ── Labels ───────────────────────────────────────────────────

    async def create_label(
        self, inbox_id: str, name: str, color: str = "#888888"
    ) -> Any:
        sdk = self._get_sdk()
        return await asyncio.to_thread(
            sdk.labels.create, inbox_id=inbox_id, name=name, color=color
        )

    async def list_labels(self, inbox_id: str) -> list[Any]:
        sdk = self._get_sdk()
        return await asyncio.to_thread(sdk.labels.list, inbox_id=inbox_id)

    async def add_label(
        self, inbox_id: str, message_id: str, label: str
    ) -> Any:
        sdk = self._get_sdk()
        return await asyncio.to_thread(
            sdk.labels.add, inbox_id=inbox_id, message_id=message_id, label=label
        )

    async def remove_label(
        self, inbox_id: str, message_id: str, label: str
    ) -> Any:
        sdk = self._get_sdk()
        return await asyncio.to_thread(
            sdk.labels.remove,
            inbox_id=inbox_id,
            message_id=message_id,
            label=label,
        )


# ── Module-level factory ────────────────────────────────────────

_v2_singleton: AgentMailV2 | None = None


def get_agentmail_v2() -> AgentMailV2:
    """Get or create the singleton ``AgentMailV2`` wrapper.

    Backwards-compatible with the old ``get_agentmail_client()`` factory.
    """
    global _v2_singleton
    if _v2_singleton is None:
        _v2_singleton = AgentMailV2()
    return _v2_singleton
