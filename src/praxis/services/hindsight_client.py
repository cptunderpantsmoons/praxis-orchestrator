"""Hindsight memory client for per-sender persistent recall.

Each email sender gets an isolated memory bank:
    bank_id = praxis:user:<normalized_sender_email>

The client wraps three Hindsight Cloud API operations:
- ``ensure_bank``   — create/update a bank with a Praxis-specific mission
- ``retain_memory`` — store an email interaction as a memory
- ``recall_memory`` — retrieve relevant memories for context injection
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_HINDSIGHT_URL = os.environ.get("HINDSIGHT_API_URL", "https://api.hindsight.vectorize.io")
_HINDSIGHT_KEY = os.environ.get("HINDSIGHT_API_KEY", "")
_BANK_PREFIX = "praxis:user:"

_PRAXIS_MISSION = (
    "Praxis email assistant memory. Retain key facts about this sender: "
    "their preferences, past inquiries, interaction history, technical "
    "context, and communication style. Ignore greetings and social niceties."
)


def _normalize_email(raw: str) -> str:
    """Extract bare email from RFC 5322 ``Name <addr>`` format."""
    m = re.search(r"<([^>]+)>", raw)
    if m:
        return m.group(1).strip().lower()
    return raw.strip().lower()


def _bank_id(sender_email: str) -> str:
    """Derive a stable Hindsight bank_id from a sender's email address."""
    return f"{_BANK_PREFIX}{_normalize_email(sender_email)}"


def _api(method: str, path: str, body: dict | None = None) -> dict:
    """Make an authenticated Hindsight API request."""
    url = f"{_HINDSIGHT_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_HINDSIGHT_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()[:300]
        logger.warning("hindsight.api_error", method=method, path=path, status=e.code, error=error_body)
        return {"error": error_body, "status": e.code}
    except Exception as e:
        logger.warning("hindsight.request_failed", method=method, path=path, error=str(e))
        return {"error": str(e)}


async def ensure_bank(sender_email: str) -> str:
    """Create or update the memory bank for a sender. Returns the bank_id."""
    bank_id = _bank_id(sender_email)
    if not _HINDSIGHT_KEY:
        logger.debug("hindsight.no_key", bank_id=bank_id)
        return bank_id
    result = _api("PUT", f"/v1/default/banks/{bank_id}", {"retain_mission": _PRAXIS_MISSION})
    if "error" not in result:
        logger.info("hindsight.bank_ready", bank_id=bank_id)
    return bank_id


async def retain_memory(sender_email: str, content: str) -> dict[str, Any]:
    """Store an interaction as a memory in the sender's bank.

    Args:
        sender_email: The sender's email address (raw or normalized).
        content: A natural-language summary of the interaction, e.g.
                 "User asked about X. Praxis replied with Y."
    """
    bank_id = _bank_id(sender_email)
    if not _HINDSIGHT_KEY:
        return {"skipped": True, "reason": "no_api_key"}
    result = _api(
        "POST",
        f"/v1/default/banks/{bank_id}/memories",
        {"items": [{"content": content}]},
    )
    if "error" not in result:
        logger.info("hindsight.retained", bank_id=bank_id, success=result.get("success", False))
    return result


async def recall_memory(sender_email: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Retrieve relevant memories for a sender.

    Args:
        sender_email: The sender's email address.
        query: A natural-language query (typically the email subject + body).
        limit: Maximum number of results to return.

    Returns:
        A list of memory result dicts, each with ``text``, ``type``, and
        ``entities`` fields. Returns an empty list on error or if no API key.
    """
    bank_id = _bank_id(sender_email)
    if not _HINDSIGHT_KEY:
        return []
    result = _api(
        "POST",
        f"/v1/default/banks/{bank_id}/memories/recall",
        {"query": query},
    )
    if "error" in result:
        logger.warning("hindsight.recall_failed", bank_id=bank_id, error=result["error"])
        return []
    memories = result.get("results", [])
    logger.info("hindsight.recalled", bank_id=bank_id, count=len(memories))
    return memories[:limit]
