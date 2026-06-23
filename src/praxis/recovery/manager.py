"""RecoveryManager — owns the background worker that processes failure events.

The manager is constructed once in ``lifespan()`` and stored on
``app.state.recovery_manager``. ``capture_failure()`` is synchronous and
fire-and-forget — it pushes to the queue and increments metrics but never
blocks the ReAct loop. The background ``_run()`` loop polls the queue,
diagnoses each event, applies the action, and records stats.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from praxis.features.metrics import get_metrics
from praxis.recovery.actions import ActionDispatcher, ActionResult
from praxis.recovery.diagnosis import Diagnoser
from praxis.recovery.models import (
    FailureContext,
    RecoveryActionType,
    RecoveryEvent,
    RecoveryPattern,
    RecoveryStats,
)
from praxis.recovery.queue import RecoveryQueue

if TYPE_CHECKING:
    from fastapi import FastAPI
    from praxis.config import Settings

logger = structlog.get_logger()

# Strong references to fire-and-forget background tasks (enable/disable).
# Without these, CPython's asyncio may GC tasks mid-execution.
_bg_tasks: set[asyncio.Task] = set()


class _RateLimiter:
    """Enforces a minimum interval between action executions."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = min_interval_seconds
        self._last_action_time: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_action_time
            if elapsed < self._min_interval:
                wait = self._min_interval - elapsed
                logger.debug("recovery.rate_limit_wait", wait_seconds=round(wait, 2))
                await asyncio.sleep(wait)
            self._last_action_time = time.monotonic()


class _StatsTracker:
    """Tracks aggregate recovery stats in memory."""

    def __init__(self) -> None:
        self.total_failures = 0
        self.total_recovered = 0
        self.total_failed = 0
        self.total_escalated = 0
        self.by_pattern: dict[str, dict[str, int]] = {}  # pattern -> {attempts, successes, failures}
        self.by_tool: dict[str, int] = {}
        self.last_processed_at: str | None = None

    def record(self, event: RecoveryEvent) -> None:
        self.by_tool[event.failure.tool_name] = self.by_tool.get(event.failure.tool_name, 0) + 1
        pattern_key = str(event.pattern)
        bucket = self.by_pattern.setdefault(pattern_key, {"attempts": 0, "successes": 0, "failures": 0})
        bucket["attempts"] += 1
        if event.status == "recovered":
            self.total_recovered += 1
            bucket["successes"] += 1
        elif event.status == "escalated":
            self.total_escalated += 1
            bucket["failures"] += 1
        elif event.status == "failed":
            self.total_failed += 1
            bucket["failures"] += 1
        if event.processed_at is not None:
            self.last_processed_at = event.processed_at.isoformat()

    def snapshot(self, queue_size: int, worker_running: bool) -> RecoveryStats:
        return RecoveryStats(
            total_failures=self.total_failures,
            total_recovered=self.total_recovered,
            total_failed=self.total_failed,
            total_escalated=self.total_escalated,
            by_pattern=dict(self.by_pattern),
            by_tool=dict(self.by_tool),
            queue_size=queue_size,
            worker_running=worker_running,
        )


class RecoveryManager:
    """Owns the queue, background task, diagnoser, and action dispatcher."""

    def __init__(
        self,
        settings: "Settings",
        app: "FastAPI | None" = None,
    ) -> None:
        self._settings = settings
        self._app = app
        self._queue = RecoveryQueue(
            max_size=settings.recovery_queue_max_size,
            persist_path=settings.recovery_events_path,
        )
        self._diagnoser = Diagnoser()
        self._actions = ActionDispatcher(app=app)
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._enabled = settings.recovery_enabled
        self._rate_limiter = _RateLimiter(settings.recovery_rate_limit_seconds)
        self._retry_counts: dict[str, int] = {}
        self._stats = _StatsTracker()

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background worker. Idempotent — safe to call twice."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="recovery-worker")
        logger.info(
            "recovery.worker_started",
            poll_interval=self._settings.recovery_poll_interval_seconds,
        )

    async def stop(self) -> None:
        """Signal the worker to stop and wait up to 15s for graceful shutdown."""
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=15.0)
            except asyncio.TimeoutError:
                logger.warning("recovery.worker_stop_timeout")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("recovery.worker_stopped")

    def enable(self) -> None:
        """Enable the worker and start it if not already running."""
        self._enabled = True
        task = asyncio.create_task(self.start())
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)

    def disable(self) -> None:
        """Disable the worker and stop it."""
        self._enabled = False
        task = asyncio.create_task(self.stop())
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Failure capture (sync, fire-and-forget) ─────────────────

    def capture_failure(
        self,
        tool_name: str,
        error_message: str,
        args: dict[str, Any] | None = None,
        thread_id: str = "",
        event_id: str = "",
        raw_result_json: str = "",
        traceback_str: str = "",
        model_name: str = "",
    ) -> None:
        """Enqueue a failure for the worker to process. Never raises."""
        try:
            metrics = get_metrics()
            metrics.inc_counter(f"tool_failures:{tool_name}")
            metrics.increment_gauge("recovery_queue_size")

            event = RecoveryEvent(
                failure=FailureContext(
                    tool_name=tool_name,
                    error_message=error_message,
                    args=args or {},
                    thread_id=thread_id,
                    event_id=event_id,
                    traceback_str=traceback_str,
                    model_name=model_name,
                    raw_result_json=raw_result_json,
                )
            )
            self._queue.enqueue(event)
            self._stats.total_failures += 1
            logger.info(
                "recovery.capture",
                event_id=event.event_id,
                tool=tool_name,
                queue_size=len(self._queue),
            )
        except Exception:
            logger.debug("recovery.capture_crashed", exc_info=True)

    # ── Background poll loop ────────────────────────────────────

    async def _run(self) -> None:
        """Poll the queue, diagnose, and apply recovery actions."""
        logger.info("recovery.run_loop_started")
        while not self._stop_event.is_set():
            try:
                event = await self._queue.dequeue(
                    timeout=self._settings.recovery_poll_interval_seconds
                )
                if event is None:
                    continue
                await self._process(event)
            except asyncio.CancelledError:
                logger.info("recovery.run_loop_cancelled")
                break
            except Exception:
                logger.exception("recovery.worker_error")
                await asyncio.sleep(5)
        logger.info("recovery.run_loop_exited")

    async def _process(self, event: RecoveryEvent) -> None:
        """Diagnose and apply a recovery action to a single event."""
        metrics = get_metrics()
        metrics.decrement_gauge("recovery_queue_size")

        # 1. Diagnose
        event.pattern = self._diagnoser.diagnose(event.failure)
        event.action_type = self._diagnoser.action_for(event.pattern)
        event.status = "diagnosed"
        metrics.inc_counter(f"recovery_attempts:{event.pattern}")

        # 2. Check retry budget
        current_retries = self._retry_counts.get(event.event_id, 0)
        if current_retries >= self._settings.recovery_max_retries:
            event.status = "escalated"
            event.action_type = RecoveryActionType.ESCALATE_MANUAL
            metrics.inc_counter(f"recovery_escalated:{event.pattern}")
            event.processed_at = self._now()
            self._queue._persist(event)
            self._stats.record(event)
            logger.warning(
                "recovery.escalated_retry_budget",
                event_id=event.event_id,
                tool=event.failure.tool_name,
                retries=current_retries,
            )
            return

        # 3. Rate limit
        await self._rate_limiter.acquire()

        # 4. Execute action
        event.status = "recovering"
        try:
            result: ActionResult = await self._actions.dispatch(event)
            event.result_success = result.success
            event.result_message = result.message
            if result.success:
                event.status = "recovered"
                metrics.inc_counter(f"recovery_successes:{event.pattern}")
                # Reset retry count on success
                self._retry_counts.pop(event.event_id, None)
            else:
                event.status = "failed"
                metrics.inc_counter(f"recovery_failures:{event.pattern}")
                self._retry_counts[event.event_id] = current_retries + 1
                event.retry_count = self._retry_counts[event.event_id]
                # Re-enqueue if budget remains (<= so the next dequeue hits the
                # escalation path when current_retries >= max_retries)
                if event.retry_count <= self._settings.recovery_max_retries:
                    logger.info(
                        "recovery.reenqueue",
                        event_id=event.event_id,
                        retry_count=event.retry_count,
                    )
                    self._queue.enqueue(event)
                    metrics.increment_gauge("recovery_queue_size")
        except Exception as exc:
            event.status = "failed"
            event.result_success = False
            event.result_message = f"Recovery action crashed: {exc}"
            metrics.inc_counter(f"recovery_failures:{event.pattern}")
            logger.exception("recovery.action_crashed", event_id=event.event_id)

        event.processed_at = self._now()
        self._queue._persist(event)
        self._stats.record(event)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    # ── Query API (for admin endpoints) ────────────────────────

    def list_events(
        self,
        limit: int = 100,
        status: str | None = None,
        pattern: str | None = None,
    ) -> list[RecoveryEvent]:
        """Return up to ``limit`` recent events, optionally filtered."""
        events = self._queue.snapshot(limit=limit if limit > 0 else 100)
        if status is not None:
            events = [e for e in events if e.status == status]
        if pattern is not None:
            events = [e for e in events if str(e.pattern) == pattern]
        return events

    def stats(self) -> RecoveryStats:
        return self._stats.snapshot(
            queue_size=len(self._queue),
            worker_running=self._task is not None and not self._task.done(),
        )

    def health_status(self) -> dict[str, Any]:
        return {
            "worker_running": self._task is not None and not self._task.done(),
            "enabled": self._enabled,
            "queue_size": len(self._queue),
            "last_processed_at": self._stats.last_processed_at,
            "poll_interval_seconds": self._settings.recovery_poll_interval_seconds,
            "rate_limit_seconds": self._settings.recovery_rate_limit_seconds,
            "max_retries": self._settings.recovery_max_retries,
        }
