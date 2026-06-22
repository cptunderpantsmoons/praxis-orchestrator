"""GET /admin/logs/audit — paginated audit log reader with filters.

GET /admin/logs/audit reads the JSONL audit log (most-recent-first) and
applies optional ``since``/``level``/``event``/``limit`` filters.

GET /admin/logs/tail is a server-sent events stream of structlog output.
Phase C's TUI consumes ``/audit`` for the logs screen; the SSE stream is
provided for future live-tail use. The current implementation streams the
contents of an in-memory ring buffer that other components can push lines
into via ``push_log_line``; if nothing has been pushed it emits a single
"ready" comment and keeps the connection open.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query
from sse_starlette.sse import EventSourceResponse

from praxis.webhooks.auth import require_admin_token

router = APIRouter(prefix="/admin/logs", tags=["admin"])

# In-memory ring buffer for the SSE tail endpoint. Components that want their
# log lines streamed to the TUI can call ``push_log_line``; the buffer keeps
# the last 1000 lines.
_RECENT_LINES: deque[str] = deque(maxlen=1000)
_NEW_LINES: asyncio.Event = asyncio.Event()
_PENDING: deque[str] = deque()


def push_log_line(line: str) -> None:
    """Append a log line to the SSE ring buffer and wake any tail clients."""
    _RECENT_LINES.append(line)
    _PENDING.append(line)
    _NEW_LINES.set()


def _audit_log_path() -> Path:
    """Return the path to the audit log JSONL file.

    Pulled out as a module-level function so tests can monkeypatch it.
    """
    return Path("logs/audit_log.jsonl")


@router.get("/audit")
async def get_audit_logs(
    _: None = Depends(require_admin_token),
    since: str | None = Query(
        None, description="ISO-8601 timestamp; entries with ts < since are excluded"
    ),
    level: str | None = Query(
        None, description="Only return entries whose level matches (case-insensitive)"
    ),
    event: str | None = Query(
        None,
        description="Substring filter on the event field (case-insensitive)",
    ),
    limit: int = Query(100, ge=1, le=1000, description="Max entries to return"),
) -> dict[str, Any]:
    """Read the audit log, newest-first, with optional filters."""
    path = _audit_log_path()
    if not path.exists():
        return {"entries": []}

    entries: list[dict[str, Any]] = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if since and entry.get("ts", "") < since:
            continue
        if level and entry.get("level", "").lower() != level.lower():
            continue
        if event and event.lower() not in entry.get("event", "").lower():
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    return {"entries": entries}


@router.get("/tail")
async def tail_logs(_: None = Depends(require_admin_token)) -> EventSourceResponse:
    """SSE stream of recent log lines.

    Emits the current ring buffer contents immediately, then streams new lines
    as they arrive via ``push_log_line``.
    """

    async def event_stream() -> AsyncGenerator[dict[str, str], None]:
        # Replay the current buffer.
        for line in list(_RECENT_LINES):
            yield {"event": "log", "data": line}
        # Stream new lines as they arrive.
        seen = len(_RECENT_LINES)
        while True:
            await _NEW_LINES.wait()
            _NEW_LINES.clear()
            # Drain anything queued since the last yield.
            new: list[str] = []
            while _PENDING:
                new.append(_PENDING.popleft())
            for line in new:
                yield {"event": "log", "data": line}
            # Defensive cap: if the buffer has grown beyond what we've seen,
            # advance our cursor so we don't replay on the next wake.
            if len(_RECENT_LINES) > seen:
                seen = len(_RECENT_LINES)
            await asyncio.sleep(0)

    return EventSourceResponse(event_stream())
