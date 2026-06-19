"""Quality Gate 1: Concurrency semaphore enforcement under concurrent load.

These are the critical Phase 1 tests that prove the UmansConcurrencyRouter
strictly enforces per-model-family concurrency limits (4 for Kimi/GLM,
8 for Qwen) when many requests arrive simultaneously.
"""

import asyncio

import httpx
import pytest

from praxis.config import Settings
from praxis.router import UmansConcurrencyRouter


def _make_router(**overrides) -> UmansConcurrencyRouter:
    """Create a router with test settings (no real HTTP calls).

    Separates Settings kwargs from Router kwargs:
    - http_client goes to UmansConcurrencyRouter
    - everything else goes to Settings
    """
    router_kwargs: dict = {}
    if "http_client" in overrides:
        router_kwargs["http_client"] = overrides.pop("http_client")

    settings = Settings(
        environment="test",
        umans_api_key="test-key",
        umans_base_url="http://test-umans.local/v1",
        **overrides,
    )
    return UmansConcurrencyRouter(settings=settings, **router_kwargs)


def _tracking_mock():
    """Return (mock_fn, state_dict) — tracks concurrent executions."""
    state = {"max": 0, "current": 0, "count": 0}

    async def mock(model_name, messages, **kwargs):
        state["current"] += 1
        state["count"] += 1
        state["max"] = max(state["max"], state["current"])
        await asyncio.sleep(0.05)  # ensure overlap
        state["current"] -= 1
        return {"choices": [{"message": {"content": f"ok-{model_name}"}}]}

    return mock, state


# ── Semaphore Limit Enforcement ───────────────────────────────────


@pytest.mark.asyncio
async def test_qwen_enforces_limit_8():
    """16 concurrent umans-flash calls: peak concurrency must be exactly 8."""
    router = _make_router()
    mock, state = _tracking_mock()
    router._call_api = mock  # type: ignore[assignment]

    tasks = [
        router.invoke("umans-flash", [{"role": "user", "content": f"r{i}"}]) for i in range(16)
    ]
    await asyncio.gather(*tasks)

    assert state["max"] <= 8, f"Peak {state['max']} exceeded limit 8"
    assert state["max"] == 8, f"Expected peak 8, got {state['max']}"
    assert state["count"] == 16, "All 16 must complete"
    assert router.get_peak("umans-flash") == 8


@pytest.mark.asyncio
async def test_kimi_enforces_limit_4():
    """12 concurrent umans-coder calls: peak concurrency must be exactly 4."""
    router = _make_router()
    mock, state = _tracking_mock()
    router._call_api = mock  # type: ignore[assignment]

    tasks = [
        router.invoke("umans-coder", [{"role": "user", "content": f"r{i}"}]) for i in range(12)
    ]
    await asyncio.gather(*tasks)

    assert state["max"] <= 4, f"Peak {state['max']} exceeded limit 4"
    assert state["max"] == 4, f"Expected peak 4, got {state['max']}"
    assert router.get_peak("umans-coder") == 4


@pytest.mark.asyncio
async def test_glm_enforces_limit_4():
    """12 concurrent umans-glm-5.2 calls: peak concurrency must be exactly 4."""
    router = _make_router()
    mock, state = _tracking_mock()
    router._call_api = mock  # type: ignore[assignment]

    tasks = [
        router.invoke("umans-glm-5.2", [{"role": "user", "content": f"r{i}"}]) for i in range(12)
    ]
    await asyncio.gather(*tasks)

    assert state["max"] <= 4, f"Peak {state['max']} exceeded limit 4"
    assert state["max"] == 4, f"Expected peak 4, got {state['max']}"


# ── Family Independence ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_families_run_independently():
    """Kimi (4) + Qwen (8) limits are independent: 12 run concurrently."""
    router = _make_router()
    mock, state = _tracking_mock()
    router._call_api = mock  # type: ignore[assignment]

    tasks = [router.invoke("umans-coder", [{"role": "user", "content": "k"}]) for _ in range(4)] + [
        router.invoke("umans-flash", [{"role": "user", "content": "q"}]) for _ in range(8)
    ]
    await asyncio.gather(*tasks)

    assert state["max"] == 12, f"Expected 12 concurrent (4+8), got {state['max']}"


# ── All Requests Complete ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_dropped_requests():
    """20 concurrent requests: all must complete, none dropped."""
    router = _make_router()
    mock, state = _tracking_mock()
    router._call_api = mock  # type: ignore[assignment]

    tasks = [
        router.invoke("umans-flash", [{"role": "user", "content": f"r{i}"}]) for i in range(20)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 20
    assert state["count"] == 20


# ── Unknown Model Rejection ───────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_model_raises():
    """Unknown model names raise ValueError."""
    router = _make_router()
    with pytest.raises(ValueError, match="Unknown Umans model"):
        await router.invoke("umans-unknown", [{"role": "user", "content": "x"}])


# ── Instrumentation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_returns_to_zero():
    """get_active() returns to 0 after all requests finish."""
    router = _make_router()

    started = asyncio.Event()
    proceed = asyncio.Event()

    async def mock(model_name, messages, **kwargs):
        started.set()
        await proceed.wait()
        return {"choices": [{"message": {"content": "ok"}}]}

    router._call_api = mock  # type: ignore[assignment]

    t1 = asyncio.create_task(router.invoke("umans-flash", [{"role": "user", "content": "1"}]))
    t2 = asyncio.create_task(router.invoke("umans-flash", [{"role": "user", "content": "2"}]))

    await started.wait()
    await asyncio.sleep(0.02)
    assert router.get_active("umans-flash") == 2

    proceed.set()
    await asyncio.gather(t1, t2)

    assert router.get_active("umans-flash") == 0
    assert router.get_peak("umans-flash") == 2


def test_limits_property():
    """Router exposes per-family concurrency limits."""
    router = _make_router()
    limits = router.limits
    assert limits["qwen"] == 8
    assert limits["kimi"] == 4
    assert limits["glm"] == 4


def test_peak_starts_at_zero():
    """Peak is zero before any requests."""
    router = _make_router()
    assert router.get_peak("umans-flash") == 0
    assert router.get_active("umans-coder") == 0


# ── Close semantics ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_does_not_close_injected_client():
    """close() does NOT close a client injected from outside."""
    injected = httpx.AsyncClient()
    router = _make_router(http_client=injected)
    await router.close()
    assert not injected.is_closed
    await injected.aclose()


@pytest.mark.asyncio
async def test_close_closes_owned_client():
    """close() closes the HTTP client when owned by the router."""
    router = _make_router()
    client = await router._get_client()
    assert not client.is_closed
    await router.close()
    assert client.is_closed


# ── _call_api with mock transport ─────────────────────────────────


@pytest.mark.asyncio
async def test_call_api_with_mock_transport():
    """_call_api works with an injected mock transport client."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "mock-resp"}}]},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    router = _make_router(http_client=client)

    result = await router._call_api("umans-flash", [{"role": "user", "content": "hi"}])
    assert result["choices"][0]["message"]["content"] == "mock-resp"
    await client.aclose()


# ── Config: reset_settings ────────────────────────────────────────


def test_reset_settings():
    """reset_settings() clears the cached settings singleton."""
    from praxis.config import get_settings, reset_settings

    s1 = get_settings()
    reset_settings()
    s2 = get_settings()

    assert s1 is not s2
    assert s1.app_name == s2.app_name


# ── Concurrency with real httpx MockTransport (integration) ──────


@pytest.mark.asyncio
async def test_qwen_limit_8_with_real_transport():
    """Qwen semaphore enforcement using a real httpx MockTransport (no monkeypatch)."""
    current = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.05)
        current -= 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    router = _make_router(http_client=client)

    tasks = [
        router.invoke("umans-flash", [{"role": "user", "content": f"r{i}"}]) for i in range(16)
    ]
    await asyncio.gather(*tasks)
    await client.aclose()

    assert peak <= 8, f"Peak concurrency {peak} exceeded limit of 8"
    assert peak == 8, f"Expected peak of 8, got {peak}"
    assert router.get_peak("umans-flash") == 8


@pytest.mark.asyncio
async def test_kimi_limit_4_with_real_transport():
    """Kimi semaphore enforcement using a real httpx MockTransport (no monkeypatch)."""
    current = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.05)
        current -= 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    router = _make_router(http_client=client)

    tasks = [
        router.invoke("umans-coder", [{"role": "user", "content": f"r{i}"}]) for i in range(10)
    ]
    await asyncio.gather(*tasks)
    await client.aclose()

    assert peak <= 4, f"Peak concurrency {peak} exceeded limit of 4"
    assert peak == 4, f"Expected peak of 4, got {peak}"
    assert router.get_peak("umans-coder") == 4
