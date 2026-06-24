"""AgentMail client for sending emails from the agent's own mailbox.

The agent has dedicated inboxes at AgentMail.to — it does NOT access the user's mailbox.
This client wraps the AgentMail REST API for:
- Sending new emails
- Replying to threads
- Managing drafts

Reference: https://docs.agentmail.to/integrations/langchain
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from praxis.config import get_settings

logger = structlog.get_logger()


class AgentMailClient:
    """Async client for AgentMail API.

    The agent's mailbox is provisioned separately via AgentMail console.
    This client handles outbound operations: send, reply, draft.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.agentmail.to",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.agentmail_api_key
        self._base_url = base_url.rstrip("/")

        # Create async client with optional transport override for testing
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
            transport=transport,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def send_message(
        self,
        inbox_id: str,
        to: str,
        subject: str,
        body: str,
        *,
        html_body: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> dict[str, Any]:
        """Send a new email from the agent's inbox.

        Args:
            inbox_id: The agent's inbox ID (e.g., "ib_abc123")
            to: Recipient email address
            subject: Email subject line
            body: Plain text body
            html_body: Optional HTML body
            in_reply_to: Message-ID to reply to (for threading)
            references: References header (for threading)

        Returns:
            Message data from AgentMail API

        Raises:
            httpx.HTTPError: On API errors
        """
        payload: dict[str, Any] = {
            "to": [to] if isinstance(to, str) else to,
            "subject": subject,
            "text": body,
        }
        if html_body:
            payload["html"] = html_body
        if in_reply_to:
            payload["in_reply_to"] = in_reply_to
        if references:
            payload["references"] = references

        logger.info("agentmail.send", inbox_id=inbox_id, to=to, subject=subject)

        response = await self._client.post(
            f"/v0/inboxes/{inbox_id}/messages/send",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def reply_to_message(
        self,
        message_id: str,
        body: str,
        *,
        inbox_id: str = "",
        to: str | list[str] | None = None,
        html_body: str | None = None,
        reply_all: bool = False,
    ) -> dict[str, Any]:
        """Reply to an existing message (maintains thread).

        Args:
            message_id: The message ID to reply to
            body: Plain text reply body
            inbox_id: The agent's inbox ID (required by the v0 API path)
            to: Optional recipient list. The v0 reply endpoint accepts ``to``
                as a list of emails; if omitted, AgentMail replies to the
                original sender.
            html_body: Optional HTML body
            reply_all: Whether to reply to all recipients

        Returns:
            Reply message data from AgentMail API
        """
        payload: dict[str, Any] = {
            "text": body,
            "reply_all": reply_all,
        }
        if to is not None:
            payload["to"] = [to] if isinstance(to, str) else to
        if html_body:
            payload["html"] = html_body

        logger.info(
            "agentmail.reply",
            message_id=message_id,
            inbox_id=inbox_id,
            reply_all=reply_all,
        )

        response = await self._client.post(
            f"/v0/inboxes/{inbox_id}/messages/{message_id}/reply",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    async def list_inboxes(self) -> list[dict[str, Any]]:
        """List all inboxes owned by the agent account."""
        response = await self._client.get("/v0/inboxes")
        response.raise_for_status()
        return response.json()

    async def create_inbox(self, *, username: str | None = None) -> dict[str, Any]:
        """Create a new inbox for the agent.

        Args:
            username: Optional custom username (random if not provided)

        Returns:
            Inbox data including id and address
        """
        payload = {"username": username} if username else {}
        response = await self._client.post("/v0/inboxes", json=payload)
        response.raise_for_status()
        return response.json()

    async def get_inbox(self, inbox_id: str) -> dict[str, Any]:
        """Get details for a specific inbox."""
        response = await self._client.get(f"/v0/inboxes/{inbox_id}")
        response.raise_for_status()
        return response.json()


# Singleton instance for tools
_client: AgentMailClient | None = None


def get_agentmail_client() -> AgentMailClient:
    """Get or create the singleton AgentMail client."""
    global _client
    if _client is None:
        _client = AgentMailClient()
    return _client
