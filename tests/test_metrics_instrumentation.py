"""Tests for metrics instrumentation in the router and chat model."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from praxis.config import reset_settings
from praxis.features.metrics import MetricsRegistry
from praxis.router import UmansConcurrencyRouter


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


@pytest.mark.asyncio
async def test_router_updates_gauges_on_acquire_release(monkeypatch):
    # Inject a fresh registry.
    registry = MetricsRegistry()
    monkeypatch.setattr("praxis.router.get_metrics", lambda: registry)

    router = UmansConcurrencyRouter()
    async with router.acquire("umans-flash"):
        snap = registry.snapshot()
        assert snap["gauges"].get("router_active:umans-flash") == 1
        assert snap["gauges"].get("router_global_active") == 1
    snap = registry.snapshot()
    assert snap["gauges"].get("router_active:umans-flash") == 0
    assert snap["gauges"].get("router_global_active") == 0
    # Peak should persist
    assert snap["gauges"].get("router_peak:umans-flash") == 1


@pytest.mark.asyncio
async def test_chat_model_increments_counter_and_records_latency(monkeypatch):
    registry = MetricsRegistry()
    monkeypatch.setattr("praxis.chat.wrappers.get_metrics", lambda: registry)

    response = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}], "model": "umans-flash"}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=response))
    router = UmansConcurrencyRouter()
    router._client = httpx.AsyncClient(transport=transport)  # type: ignore[attr-defined]

    from praxis.chat.wrappers import UmansChatModel
    model = UmansChatModel.create("umans-flash", router=router)
    from langchain_core.messages import HumanMessage
    await model.ainvoke([HumanMessage(content="hi")])

    snap = registry.snapshot()
    assert snap["counters"].get("model_calls:umans-flash") == 1
    assert "model_latency_ms:umans-flash" in snap["histograms"]
