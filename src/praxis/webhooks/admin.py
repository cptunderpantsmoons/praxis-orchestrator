"""Administrative endpoints for dead-letter event management and recovery.

These endpoints are intended for operators to replay webhooks that failed
in ``/webhook/email``. Failed events are written to the JSONL path configured
by ``Settings.failed_events_path`` (default: ``/tmp/praxis_failed_events.jsonl``).
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from typing import Any

import orjson
import structlog
from fastapi import APIRouter, HTTPException, Request, status

from praxis.config import get_settings
from praxis.webhooks.email import (
    _failed_events_path,
    _parse_inbound_email,
    _process_received_email,
)
from praxis.webhooks.payloads import parse_agentmail_event

logger = structlog.get_logger()

router = APIRouter(prefix="/admin", tags=["admin"])


def _read_failed_events(settings: Any) -> list[dict[str, Any]]:
    path = _failed_events_path(settings)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(orjson.loads(line))
            except orjson.JSONDecodeError:
                logger.warning("failed_events.invalid_json_line", line=line[:200])
    return list(reversed(events))


def _mark_event_resolved(path: pathlib.Path, event_id: str) -> None:
    """Rewrite the dead-letter log, dropping all records for ``event_id``."""
    if not path.exists():
        return
    kept: list[bytes] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = orjson.loads(line)
            except orjson.JSONDecodeError:
                kept.append(line.encode("utf-8"))
                continue
            if str(entry.get("event_id", "")) == event_id:
                continue
            kept.append(line.encode("utf-8"))
    path.write_bytes(b"".join(kept))


@router.get("/failed-events")
async def list_failed_events() -> dict[str, Any]:
    """List webhook events that failed and were persisted for retry."""
    settings = get_settings()
    events = _read_failed_events(settings)
    return {
        "count": len(events),
        "path": str(_failed_events_path(settings)),
        "events": [
            {
                "index": idx,
                "event_id": e.get("event_id"),
                "thread_id": e.get("thread_id"),
                "timestamp": e.get("timestamp"),
                "error": e.get("error"),
            }
            for idx, e in enumerate(events)
        ],
    }


@router.post("/retry-failed")
async def retry_failed_event(
    request: Request,
    event_id: str | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    """Replay a failed webhook event.

    Specify either ``event_id`` or ``index`` (most recent = 0). If neither is
    provided, the most recent failed event is retried.
    """
    settings = get_settings()
    events = _read_failed_events(settings)
    if not events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No failed events found",
        )

    target: dict[str, Any] | None = None
    if event_id:
        for e in events:
            if str(e.get("event_id", "")) == event_id:
                target = e
                break
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Failed event not found: {event_id}",
            )
    elif index is not None:
        if index < 0 or index >= len(events):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid index {index}; {len(events)} events available",
            )
        target = events[index]
    else:
        target = events[0]

    raw_payload = target.get("payload", "")
    if not raw_payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed event has no stored payload",
        )
    raw_body = raw_payload.encode("utf-8")

    try:
        payload = orjson.loads(raw_body)
    except orjson.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stored payload is not valid JSON: {exc}",
        ) from exc

    parsed = parse_agentmail_event(payload)
    if parsed is None:
        event_type_raw = payload.get("event_type") or payload.get("type", "")
        if not event_type_raw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Payload shape unrecognised",
            )
        email = _parse_inbound_email(payload)
        _event_id = str(payload.get("id", "") or payload.get("event_id", ""))
        thread_id = f"thread_{_event_id}" if _event_id else f"thread_retry_{datetime.now(UTC).isoformat()}"
        event_id_out = target.get("event_id", _event_id)
    else:
        if not parsed.is_received():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Event is not a received message: {parsed.event_type}",
            )
        email = parsed.to_inbound_email()
        if email is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parsed received event has no email payload",
            )
        event_id_out = target.get("event_id") or parsed.event_id
        thread_id = (
            f"thread_{parsed.thread_id}"
            if parsed.thread_id
            else f"thread_{event_id_out or parsed.event_id}"
        )

    result = await _process_received_email(
        app=request.app,
        email=email,
        event_id=event_id_out,
        thread_id=thread_id,
        settings=settings,
        raw_body=raw_body,
        is_retry=True,
    )

    if result.status == "accepted_retry":
        _mark_event_resolved(_failed_events_path(settings), event_id_out)

    return {
        "replayed_event_id": event_id_out,
        "thread_id": thread_id,
        "status": result.status,
        "message": result.message,
    }
