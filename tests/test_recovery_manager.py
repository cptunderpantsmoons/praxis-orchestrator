"""Tests for the RecoveryManager worker loop."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from praxis.recovery.actions import ActionResult
from praxis.recovery.manager import RecoveryManager, _RateLimiter, _StatsTracker
from praxis.recovery.models import FailureContext, RecoveryEvent, RecoveryPattern


class _MockSettings:
    recovery_enabled = True
    recovery_poll_interval_seconds = 0.05
    recovery_max_retries = 3
    recovery_rate_limit_seconds = 0.0  # No rate limiting in tests
    recovery_events_path = "logs/test_recovery_events.jsonl"
    recovery_queue_max_size = 100


@pytest.fixture(autouse=True)
def _reset_metrics():
    from praxis.features.metrics import get_metrics
    get_metrics().reset()
    yield
    get_metrics().reset()


@pytest.fixture(autouse=True)
def _cleanup_test_log(tmp_path, monkeypatch):
    """Redirect recovery_events_path to tmp_path so tests don't pollute logs/."""
    log_path = tmp_path / "recovery_events.jsonl"
    monkeypatch.setattr(_MockSettings, "recovery_events_path", str(log_path))
    yield


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_no_wait_on_first_acquire(self):
        rl = _RateLimiter(min_interval_seconds=1.0)
        # First acquire should return immediately
        start = time.monotonic()
        await rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_waits_between_acquires(self):
        rl = _RateLimiter(min_interval_seconds=0.2)
        await rl.acquire()
        start = time.monotonic()
        await rl.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.15  # ~0.2s minus tolerance


class TestStatsTracker:
    def test_records_success(self):
        s = _StatsTracker()
        event = RecoveryEvent(
            failure=FailureContext(tool_name="list_agents", error_message="x", args={}),
            pattern=RecoveryPattern.TOOL_RETURNED_EMPTY,
            status="recovered",
        )
        event.processed_at = datetime.now(UTC)
        s.record(event)
        assert s.total_recovered == 1
        assert s.by_pattern["tool_returned_empty"]["successes"] == 1
        assert s.by_tool["list_agents"] == 1

    def test_records_escalation(self):
        s = _StatsTracker()
        event = RecoveryEvent(
            failure=FailureContext(tool_name="mystery", error_message="x", args={}),
            pattern=RecoveryPattern.UNKNOWN,
            status="escalated",
        )
        event.processed_at = datetime.now(UTC)
        s.record(event)
        assert s.total_escalated == 1
        assert s.by_pattern["unknown"]["failures"] == 1

    def test_snapshot(self):
        s = _StatsTracker()
        s.total_failures = 5
        s.total_recovered = 3
        snap = s.snapshot(queue_size=2, worker_running=True)
        assert snap.total_failures == 5
        assert snap.total_recovered == 3
        assert snap.queue_size == 2
        assert snap.worker_running is True


class TestCaptureFailure:
    @pytest.mark.asyncio
    async def test_capture_enqueue_event_and_increments_metrics(self):
        mgr = RecoveryManager(_MockSettings(), app=None)
        mgr.capture_failure(
            tool_name="reply_email",
            error_message="send failed",
            args={"to": "user@example.com"},
        )
        assert len(mgr._queue) == 1
        from praxis.features.metrics import get_metrics
        m = get_metrics()
        assert m._counters.get("tool_failures:reply_email") == 1
        assert m._gauges.get("recovery_queue_size") == 1
        assert mgr._stats.total_failures == 1

    @pytest.mark.asyncio
    async def test_capture_never_raises(self):
        """If capture_failure crashes internally, it must not propagate."""
        mgr = RecoveryManager(_MockSettings(), app=None)
        # Force a crash by making the queue raise
        def boom(event):
            raise RuntimeError("queue broken")
        mgr._queue.enqueue = boom  # type: ignore[method-assign]
        # Should NOT raise
        mgr.capture_failure(tool_name="x", error_message="y")
        # Metrics were incremented before the crash, but the event wasn't enqueued
        from praxis.features.metrics import get_metrics
        assert get_metrics()._counters.get("tool_failures:x") == 1


class TestWorkerLoop:
    @pytest.mark.asyncio
    async def test_worker_processes_and_recovers(self):
        mgr = RecoveryManager(_MockSettings(), app=None)
        # Mock the action dispatcher to always succeed
        mock_result = ActionResult(success=True, message="fixed it")
        mgr._actions.dispatch = AsyncMock(return_value=mock_result)

        await mgr.start()
        try:
            mgr.capture_failure(
                tool_name="list_agents",
                error_message="empty result",
                args={"region": "Australia"},
            )
            # Wait for processing
            await asyncio.sleep(0.3)
            stats = mgr.stats()
            assert stats.total_failures >= 1
            assert stats.total_recovered >= 1
        finally:
            await mgr.stop()

    @pytest.mark.asyncio
    async def test_worker_escalates_after_max_retries(self):
        # Use a custom settings with max_retries=2
        class Settings2Retries(_MockSettings):
            recovery_max_retries = 2
        mgr = RecoveryManager(Settings2Retries(), app=None)
        # Mock dispatcher to always fail
        mock_result = ActionResult(success=False, message="still broken")
        mgr._actions.dispatch = AsyncMock(return_value=mock_result)

        await mgr.start()
        try:
            mgr.capture_failure(
                tool_name="mystery_tool",
                error_message="weird error",
                args={},
            )
            # Wait long enough for retries + escalation
            await asyncio.sleep(1.0)
            stats = mgr.stats()
            assert stats.total_escalated >= 1
        finally:
            await mgr.stop()

    @pytest.mark.asyncio
    async def test_worker_never_crashes_on_action_exception(self):
        mgr = RecoveryManager(_MockSettings(), app=None)
        # Mock dispatcher to raise
        async def crash_dispatch(event):
            raise RuntimeError("action crashed")
        mgr._actions.dispatch = crash_dispatch  # type: ignore[method-assign]

        await mgr.start()
        try:
            mgr.capture_failure(
                tool_name="list_agents",
                error_message="empty",
                args={"region": "AU"},
            )
            await asyncio.sleep(0.5)
            # Worker should still be running
            assert mgr._task is not None and not mgr._task.done()
        finally:
            await mgr.stop()

    @pytest.mark.asyncio
    async def test_worker_stops_gracefully(self):
        mgr = RecoveryManager(_MockSettings(), app=None)
        mgr._actions.dispatch = AsyncMock(return_value=ActionResult(success=True, message="ok"))
        await mgr.start()
        assert mgr._task is not None
        await mgr.stop()
        assert mgr._task is None

    @pytest.mark.asyncio
    async def test_enable_disable_toggles_worker(self):
        mgr = RecoveryManager(_MockSettings(), app=None)
        mgr._actions.dispatch = AsyncMock(return_value=ActionResult(success=True, message="ok"))
        # Start disabled
        await mgr.stop()  # ensure stopped
        assert mgr._task is None or mgr._task.done()
        # Enable — should start the worker
        mgr.enable()
        await asyncio.sleep(0.1)
        assert mgr._task is not None and not mgr._task.done()
        # Disable — should stop the worker
        mgr.disable()
        await asyncio.sleep(0.2)
        assert mgr._task is None or mgr._task.done()


class TestQueryApi:
    @pytest.mark.asyncio
    async def test_list_events_returns_snapshot(self):
        mgr = RecoveryManager(_MockSettings(), app=None)
        mgr.capture_failure(tool_name="t1", error_message="e1", args={})
        mgr.capture_failure(tool_name="t2", error_message="e2", args={})
        events = mgr.list_events(limit=10)
        assert len(events) == 2
        # Most recent first
        assert events[0].failure.tool_name == "t2"

    @pytest.mark.asyncio
    async def test_list_events_filter_by_status(self):
        mgr = RecoveryManager(_MockSettings(), app=None)
        mgr.capture_failure(tool_name="t1", error_message="e1", args={})
        # Events are "pending" status until processed
        events = mgr.list_events(limit=10, status="pending")
        assert len(events) == 1
        events = mgr.list_events(limit=10, status="recovered")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_health_status(self):
        mgr = RecoveryManager(_MockSettings(), app=None)
        health = mgr.health_status()
        assert health["worker_running"] is False
        assert health["enabled"] is True
        assert health["queue_size"] == 0
        assert health["poll_interval_seconds"] == 0.05
        assert health["max_retries"] == 3
