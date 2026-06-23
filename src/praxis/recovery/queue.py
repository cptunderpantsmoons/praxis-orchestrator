"""Asyncio queue for recovery events with JSONL persistence."""

from __future__ import annotations

import asyncio
import pathlib
from collections import deque

import orjson
import structlog

from praxis.recovery.models import RecoveryEvent

logger = structlog.get_logger()


class RecoveryQueue:
    """Asyncio deque with max size and JSONL persistence.

    - ``enqueue`` is synchronous (called from the ReAct loop's tool wrapper).
      It appends to the deque and persists to disk. If the deque is full,
      the oldest event is dropped (logged) and the new one appended.
    - ``dequeue`` is async and blocks up to ``timeout`` seconds for a new
      event. Returns None on timeout so the worker can check its stop event.
    """

    def __init__(
        self,
        max_size: int = 500,
        persist_path: str | pathlib.Path = "logs/recovery_events.jsonl",
    ) -> None:
        self._deque: deque[RecoveryEvent] = deque(maxlen=max_size)
        self._event = asyncio.Event()
        self._persist_path = pathlib.Path(persist_path)
        self._persistence_enabled = True
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as exc:
            self._persistence_enabled = False
            logger.warning(
                "recovery.persist_disabled",
                path=str(self._persist_path),
                error=str(exc),
            )

    def enqueue(self, event: RecoveryEvent) -> None:
        """Add an event. Synchronous — safe to call from sync code.

        If the deque is full, deque(maxlen=...) silently drops the oldest.
        We detect this and log it before the drop happens.
        """
        dropped = None
        if self._deque.maxlen is not None and len(self._deque) >= self._deque.maxlen:
            dropped = self._deque[0]
        self._deque.append(event)
        if dropped is not None:
            logger.warning(
                "recovery.queue_overflow",
                dropped_event_id=dropped.event_id,
                tool=dropped.failure.tool_name,
            )
        self._persist(event)
        self._event.set()

    async def dequeue(self, timeout: float = 10.0) -> RecoveryEvent | None:
        """Wait up to ``timeout`` seconds for an event. Returns None on timeout."""
        if not self._deque:
            try:
                await asyncio.wait_for(self._event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
            self._event.clear()
        if not self._deque:
            return None
        return self._deque.popleft()

    def __len__(self) -> int:
        return len(self._deque)

    def snapshot(self, limit: int = 100) -> list[RecoveryEvent]:
        """Return up to ``limit`` most recent events without removing them."""
        if limit <= 0:
            return []
        items = list(self._deque)[-limit:]
        items.reverse()
        return items

    def _persist(self, event: RecoveryEvent) -> None:
        """Append the event to the JSONL log. Failures are logged, not raised."""
        if not self._persistence_enabled:
            return
        try:
            line = orjson.dumps(event.model_dump(mode="json")).decode("utf-8")
            with self._persist_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as exc:
            logger.warning(
                "recovery.persist_failed",
                event_id=event.event_id,
                error=str(exc),
            )
