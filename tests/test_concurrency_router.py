"""Tests for UmansConcurrencyRouter semaphore enforcement — QG1 criterion 3.

Verifies that the router strictly enforces per-model-family concurrency limits
under concurrent load, and that all requests eventually complete.
"""

import asyncio

import pytest

from praxis.router.concurrency import (
    ModelFamily,
    UmansConcurrencyRouter,
)

# ── Model-family mapping tests ──────────────────────────────────────


def test_model_family_mapping():
    """Model names are correctly mapped to their families."""
    assert UmansConcurrencyRouter.get_family("umans-coder") == ModelFamily.KIMI
    assert UmansConcurrencyRouter.get_family("umans-flash") == ModelFamily.QWEN
    assert UmansConcurrencyRouter.get_family("umans-glm-5.2") == ModelFamily.GLM


def test_unknown_model_raises():
    """Unknown model names raise ValueError."""
    with pytest.raises(ValueError, match="Unknown model"):
        UmansConcurrencyRouter.get_family("umans-unknown")


def test_default_limits():
    """Default semaphore limits are 4/4/8 for Kimi/GLM/Qwen."""
    router = UmansConcurrencyRouter("http://test", "key")
    assert router.limits == {"kimi": 4, "glm": 4, "qwen": 8}


# ── Concurrency enforcement helper ───────────────────────────────────


def _make_tracking_mock():
    """Create a mock _call_api that tracks concurrent executions."""
    state = {"max": 0, "current": 0}

    async def mock_call_api(model, messages, **kwargs):
        state["current"] += 1
        state["max"] = max(state["max"], state["current"])
        await asyncio.sleep(0.05)  # Simulate API latency
        state["current"] -= 1
        return {"choices": [{"message": {"content": f"mock-{model}"}}]}

    return mock_call_api, state


# ── Qwen (umans-flash) — limit 8 ────────────────────────────────────


@pytest.mark.asyncio
async def test_qwen_semaphore_limit_8():
    """16 concurrent requests to umans-flash: max concurrent must not exceed 8."""
    router = UmansConcurrencyRouter("http://test", "key", qwen_limit=8)
    mock, state = _make_tracking_mock()
    router._call_api = mock

    tasks = [
        router.invoke("umans-flash", [{"role": "user", "content": f"test {i}"}])
        for i in range(16)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 16
    assert state["max"] <= 8, f"Max concurrent {state['max']} exceeded limit 8"
    assert state["max"] == 8, f"Expected max concurrent to reach 8, got {state['max']}"


# ── Kimi (umans-coder) — limit 4 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_kimi_semaphore_limit_4():
    """12 concurrent requests to umans-coder: max concurrent must not exceed 4."""
    router = UmansConcurrencyRouter("http://test", "key", kimi_limit=4)
    mock, state = _make_tracking_mock()
    router._call_api = mock

    tasks = [
        router.invoke("umans-coder", [{"role": "user", "content": f"test {i}"}])
        for i in range(12)
    ]
    await asyncio.gather(*tasks)

    assert state["max"] <= 4, f"Max concurrent {state['max']} exceeded limit 4"
    assert state["max"] == 4, f"Expected max concurrent to reach 4, got {state['max']}"


# ── GLM (umans-glm-5.2) — limit 4 ───────────────────────────────────


@pytest.mark.asyncio
async def test_glm_semaphore_limit_4():
    """12 concurrent requests to umans-glm-5.2: max concurrent must not exceed 4."""
    router = UmansConcurrencyRouter("http://test", "key", glm_limit=4)
    mock, state = _make_tracking_mock()
    router._call_api = mock

    tasks = [
        router.invoke("umans-glm-5.2", [{"role": "user", "content": f"test {i}"}])
        for i in range(12)
    ]
    await asyncio.gather(*tasks)

    assert state["max"] <= 4, f"Max concurrent {state['max']} exceeded limit 4"


# ── Family independence ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_families_are_independent():
    """Kimi (4) and Qwen (8) limits are independent — 12 total can run at once."""
    router = UmansConcurrencyRouter("http://test", "key")
    mock, state = _make_tracking_mock()
    router._call_api = mock

    tasks = [
        router.invoke("umans-coder", [{"role": "user", "content": "kimi"}])
        for _ in range(4)
    ] + [
        router.invoke("umans-flash", [{"role": "user", "content": "qwen"}])
        for _ in range(8)
    ]
    await asyncio.gather(*tasks)

    # All 12 run concurrently because they use different semaphores
    assert state["max"] == 12, f"Expected 12 concurrent (4+8), got {state['max']}"


# ── All requests complete ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_requests_complete():
    """20 requests with limit 2: all must complete, none dropped."""
    router = UmansConcurrencyRouter("http://test", "key", qwen_limit=2)
    call_count = 0

    async def mock_call_api(model, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return {"choices": [{"message": {"content": "ok"}}]}

    router._call_api = mock_call_api

    tasks = [
        router.invoke("umans-flash", [{"role": "user", "content": f"test {i}"}])
        for i in range(20)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 20
    assert call_count == 20


# ── Active counts tracking ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_counts_tracking():
    """active_counts property reflects in-flight requests."""
    router = UmansConcurrencyRouter("http://test", "key", qwen_limit=2)

    started = asyncio.Event()
    proceed = asyncio.Event()

    async def mock_call_api(model, messages, **kwargs):
        started.set()
        await proceed.wait()
        return {"choices": [{"message": {"content": "ok"}}]}

    router._call_api = mock_call_api

    # Start 2 requests (at the limit)
    task1 = asyncio.create_task(
        router.invoke("umans-flash", [{"role": "user", "content": "1"}])
    )
    task2 = asyncio.create_task(
        router.invoke("umans-flash", [{"role": "user", "content": "2"}])
    )

    await started.wait()
    await asyncio.sleep(0.01)  # Let both tasks enter the semaphore

    # Both should be active
    assert router.active_counts["qwen"] == 2

    proceed.set()
    await asyncio.gather(task1, task2)

    # All done — counts should be zero
    assert router.active_counts["qwen"] == 0
