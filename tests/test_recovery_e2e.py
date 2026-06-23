"""End-to-end test: failure injection through the recovery pipeline.

Verifies the full path: _execute_tool wrapper captures a failure →
RecoveryManager worker picks it up → Diagnoser classifies it →
retry_with_args action strips the region arg → _execute_tool_inner
is re-invoked with corrected args → success.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from praxis.recovery.actions import ActionDispatcher, ActionResult
from praxis.recovery.manager import RecoveryManager
from praxis.recovery.models import (
    FailureContext,
    RecoveryActionType,
    RecoveryEvent,
    RecoveryPattern,
)


class _MockSettings:
    recovery_enabled = True
    recovery_poll_interval_seconds = 0.05
    recovery_max_retries = 3
    recovery_rate_limit_seconds = 0.0
    recovery_events_path = "logs/test_recovery_e2e.jsonl"
    recovery_queue_max_size = 100


class FlakyDelegator:
    """Returns [] when region is set (simulates the region-filter bug),
    returns agents when region is None (the corrected retry)."""

    def __init__(self):
        self.call_count = 0
        self.calls: list[dict] = []

    def list_agents(self, division=None, keyword=None, region=None, limit=20):
        self.call_count += 1
        call = {"division": division, "keyword": keyword, "region": region, "limit": limit}
        self.calls.append(call)
        if region:
            return []  # The bug: region filter returns empty
        return [{"slug": "finance-analyst", "name": "Finance Analyst"}]


@pytest.fixture(autouse=True)
def _reset_metrics():
    from praxis.features.metrics import get_metrics
    get_metrics().reset()
    yield
    get_metrics().reset()


@pytest.fixture(autouse=True)
def _cleanup_test_log(tmp_path, monkeypatch):
    log_path = tmp_path / "recovery_e2e.jsonl"
    monkeypatch.setattr(_MockSettings, "recovery_events_path", str(log_path))
    yield


@pytest.mark.asyncio
async def test_list_agents_failure_triggers_recovery_and_succeeds(monkeypatch):
    """Inject a list_agents failure (empty result with region set) and verify
    the recovery worker retries with region stripped and succeeds."""
    flaky = FlakyDelegator()

    # Mock _execute_tool_inner to use our flaky delegator
    from praxis.models.schemas import AgentListingResult

    async def mock_inner(tool_call, **kwargs):
        name = tool_call.get("name") or tool_call.pop("name", "")
        if name == "list_agents":
            division = tool_call.get("division", "")
            keyword = tool_call.get("keyword", "")
            region = tool_call.get("region", "")
            delegator = kwargs.get("agent_delegator")
            if delegator is None:
                return name, AgentListingResult(success=False)
            try:
                agents = delegator.list_agents(
                    division=division or None,
                    keyword=keyword or None,
                    region=region or None,
                )
                return name, AgentListingResult(
                    division=division, keyword=keyword, region=region, agents=agents,
                    success=True if agents else False,
                )
            except Exception as exc:
                return name, AgentListingResult(success=False)
        return name, AgentListingResult(success=False)
    monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

    # Build the manager with the REAL ActionDispatcher (not mocked)
    # so we test the full retry_with_args path
    mock_app = MagicMock()
    mock_app.state.agent_delegator = flaky
    mgr = RecoveryManager(_MockSettings(), app=mock_app)

    await mgr.start()
    try:
        # Simulate the _execute_tool wrapper capturing a failure
        # (the wrapper would call capture_failure when result.success is False)
        mgr.capture_failure(
            tool_name="list_agents",
            error_message="no agents found",
            args={"division": "finance", "region": "Australia"},
            thread_id="thread_test_1",
            raw_result_json='{"agents": [], "success": false}',
        )

        # Wait for the worker to process and retry
        await asyncio.sleep(0.5)

        stats = mgr.stats()
        # The failure should have been captured
        assert stats.total_failures >= 1, f"Expected at least 1 failure, got {stats.total_failures}"
        # The recovery should have succeeded (flaky delegator returns agents when region is None)
        assert stats.total_recovered >= 1, (
            f"Expected at least 1 recovery, got {stats.total_recovered}. "
            f"Stats: {stats.model_dump()}"
        )
        # Verify the flaky delegator was called by the retry path
        # (the original failure was simulated via capture_failure, so the
        # delegator is only invoked once — during the retry_with_args action)
        assert flaky.call_count >= 1, (
            f"Expected delegator to be called >= 1 time, got {flaky.call_count}. "
            f"Calls: {flaky.calls}"
        )
        # The retry call should NOT have a region (stripped by retry_with_args)
        retry_call = flaky.calls[-1]
        assert not retry_call["region"], (
            f"Expected retry to strip region, got region={retry_call['region']!r}. "
            f"Calls: {flaky.calls}"
        )
    finally:
        await mgr.stop()


@pytest.mark.asyncio
async def test_unknown_failure_escalates_after_max_retries(monkeypatch):
    """An unknown failure pattern should escalate after max_retries attempts."""
    from praxis.models.schemas import CalculationResult

    async def mock_inner(tool_call, **kwargs):
        name = tool_call.get("name") or tool_call.pop("name", "")
        return name, CalculationResult(expression="x", value=0.0, success=False)
    monkeypatch.setattr("praxis.graph.nodes._execute_tool_inner", mock_inner)

    class Settings2Retries(_MockSettings):
        recovery_max_retries = 2
    mgr = RecoveryManager(Settings2Retries(), app=None)

    await mgr.start()
    try:
        mgr.capture_failure(
            tool_name="mystery_tool",
            error_message="some unknown error",
            args={},
        )
        # Wait for retries + escalation
        await asyncio.sleep(1.5)

        stats = mgr.stats()
        assert stats.total_escalated >= 1, (
            f"Expected escalation after max retries, got {stats.total_escalated}. "
            f"Stats: {stats.model_dump()}"
        )
    finally:
        await mgr.stop()
