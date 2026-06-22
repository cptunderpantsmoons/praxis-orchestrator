"""Per-sender reply style — tone, signature, language, greeting."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

import structlog
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from praxis.config import get_settings
from praxis.services.neo4j_client import Neo4jContextClient

logger = structlog.get_logger(__name__)


@dataclass
class SenderStyle:
    """Stored reply style for a single sender.

    All fields except ``updated_at`` are overridable per sender. ``updated_at``
    is refreshed on every upsert so downstream readers can detect stale entries.
    """

    tone: Literal["formal", "casual", "professional"] = "professional"
    signature: str = "— PRAXIS"
    language: str | None = None
    greeting: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


async def get_style_for_sender(sender_email: str) -> SenderStyle | None:
    """Fetch the stored style for a sender, or None if no entry exists.

    ``Neo4jContextClient`` is referenced via the module-level name so tests
    can monkeypatch ``praxis.features.sender_style.Neo4jContextClient``.
    """
    try:
        client = Neo4jContextClient()
        return await client.fetch_sender_style(sender_email)
    except Exception as exc:
        logger.warning("sender_style.fetch_failed", sender=sender_email, error=str(exc))
        return None


class UpdateSenderStyleArgs(BaseModel):
    """Arguments for the ``update_sender_style`` ReAct tool."""

    tone: Literal["formal", "casual", "professional"] = Field(
        default="professional", description="Reply tone for this sender"
    )
    signature: str = Field(default="— PRAXIS", description="Signature to append to replies")
    language: str | None = Field(default=None, description="ISO 639-1 language code")
    greeting: str | None = Field(default=None, description="Optional greeting override")
    sender_email: str = Field(..., description="Sender email to update the style for")


async def _update_sender_style(
    tone: str,
    signature: str,
    language: str | None,
    greeting: str | None,
    sender_email: str,
) -> str:
    """Persist a per-sender style to Neo4j.

    The ``sender_email`` arg is last in the signature to match the test
    fixture order; the ``UpdateSenderStyleArgs`` schema carries the canonical
    field order for the LLM.
    """
    style = SenderStyle(
        tone=tone,  # type: ignore[arg-type]
        signature=signature,
        language=language,
        greeting=greeting,
    )
    try:
        client = Neo4jContextClient()
        await client.upsert_sender_style(sender_email, style)
        return f"Sender style updated for {sender_email}"
    except Exception as exc:
        logger.error("sender_style.update_failed", sender=sender_email, error=str(exc))
        return f"Failed to update sender style: {exc}"


update_sender_style_tool = StructuredTool.from_function(
    _update_sender_style,
    name="update_sender_style",
    description="Update the reply style (tone, signature, language, greeting) for a specific sender.",
    args_schema=UpdateSenderStyleArgs,
    coroutine=_update_sender_style,
)


__all__ = [
    "SenderStyle",
    "UpdateSenderStyleArgs",
    "get_style_for_sender",
    "update_sender_style_tool",
]
