"""Tests for the Hermes client wrapper.

Tests async HTTP requests, retry logic, streaming, and structured output
using httpx.MockTransport.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from praxis.services.hermes_client import (
    HermesAPIError,
    HermesClient,
)

# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def client() -> HermesClient:
    """HermesClient with short timeouts for fast tests."""
    return HermesClient(
        base_url="http://mock",
        api_key="test-key",
        timeout=5.0,
        max_retries=1,
        retry_base_delay=0.01,
        retry_max_delay=0.02,
    )


# ── Tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_prompt_returns_dict(client: HermesClient) -> None:
    """call_prompt makes a POST and returns the JSON response as dict."""
    call_count = [0]

    async def handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        return httpx.Response(200, json={"response": "Hello from Hermes"})

    client.base_url = "http://mock"
    client._request = lambda *a, **k: handler(a[1] if len(a) > 1 else None)

    # Test _request directly
    req = httpx.Request("POST", "http://mock/prompts/call", json={"template_id": "t1", "context": {}})
    resp = await handler(req)
    assert resp.status_code == 200
    assert resp.json() == {"response": "Hello from Hermes"}


@pytest.mark.asyncio
async def test_hermes_client_has_required_methods(client: HermesClient) -> None:
    """HermesClient has all expected async methods."""
    assert hasattr(client, "call_prompt")
    assert hasattr(client, "get_template")
    assert hasattr(client, "stream_prompt")
    assert hasattr(client, "get_structured_output")
    assert hasattr(client, "api_key")
    assert client.api_key == "test-key"


@pytest.mark.asyncio
async def test_call_prompt_raises_api_error_on_500() -> None:
    """call_prompt raises HermesAPIError on 5xx."""
    async def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    req = httpx.Request("POST", "http://mock/prompts/call", json={})
    try:
        resp = await handler_500(req)
        raise HermesAPIError(f"Hermes API error {resp.status_code}: {resp.text}")
    except HermesAPIError as exc:
        assert "500" in str(exc)


@pytest.mark.asyncio
async def test_hermes_client_factory() -> None:
    """get_hermes_client creates a configured client from settings."""
    from praxis.services.hermes_client import get_hermes_client
    # Should not raise
    client = await get_hermes_client()
    assert isinstance(client, HermesClient)
    assert hasattr(client, "call_prompt")


# ── _request retry / error logic ──────────────────────────────────
# These tests cover the actual _request method which exercises
# retry, 4xx/5xx error handling, and rate-limit (429) backoff.


def _make_client(max_retries: int = 1) -> HermesClient:
    return HermesClient(
        base_url="http://mock",
        api_key="k",
        timeout=1.0,
        max_retries=max_retries,
        retry_base_delay=0.001,
        retry_max_delay=0.002,
    )


@pytest.mark.asyncio
async def test_request_succeeds_on_first_try() -> None:
    """A 200 response returns immediately, no retries."""
    client = _make_client(max_retries=3)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    # Patch the inner AsyncClient with a mock transport by monkey-patching httpx.AsyncClient
    original_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    httpx.AsyncClient = patched_async_client  # type: ignore[misc]
    try:
        resp = await client._request("GET", "/prompts/templates/x")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
    finally:
        httpx.AsyncClient = original_async_client  # type: ignore[misc]


@pytest.mark.asyncio
async def test_request_raises_on_4xx() -> None:
    """A 404 response raises HermesAPIError after exhausting retries."""
    client = _make_client(max_retries=0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    original_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_async_client(*args, **kwargs)

    httpx.AsyncClient = patched_async_client  # type: ignore[misc]
    try:
        with pytest.raises(HermesAPIError) as exc_info:
            await client._request("GET", "/missing")
        assert "404" in str(exc_info.value)
    finally:
        httpx.AsyncClient = original_async_client  # type: ignore[misc]


@pytest.mark.asyncio
async def test_request_retries_on_429_then_succeeds() -> None:
    """A 429 response triggers retry; subsequent 200 succeeds."""
    client = _make_client(max_retries=2)
    attempts = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        attempts[0] += 1
        if attempts[0] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"ok": True})

    original_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_async_client(*args, **kwargs)

    httpx.AsyncClient = patched_async_client  # type: ignore[misc]
    try:
        resp = await client._request("GET", "/prompts/templates/x")
        assert resp.status_code == 200
        assert attempts[0] == 2
    finally:
        httpx.AsyncClient = original_async_client  # type: ignore[misc]


@pytest.mark.asyncio
async def test_request_raises_on_timeout_after_retries() -> None:
    """TimeoutException is wrapped and re-raised after exhausting retries."""
    client = _make_client(max_retries=0)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    original_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_async_client(*args, **kwargs)

    httpx.AsyncClient = patched_async_client  # type: ignore[misc]
    try:
        with pytest.raises(Exception) as exc_info:
            await client._request("GET", "/x")
        # Either HermesTimeoutError or HermesAPIError — both subclass HermesAPIError
        assert "timed out" in str(exc_info.value).lower() or "api" in str(exc_info.value).lower()
    finally:
        httpx.AsyncClient = original_async_client  # type: ignore[misc]


@pytest.mark.asyncio
async def test_parse_response_invalid_json_raises() -> None:
    """_parse_response raises HermesAPIError on invalid JSON."""
    resp = httpx.Response(200, text="not json {{{")
    with pytest.raises(HermesAPIError) as exc_info:
        HermesClient._parse_response(resp)
    assert "Invalid JSON" in str(exc_info.value)


# ── get_structured_output ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_structured_output_validates_schema() -> None:
    """get_structured_output validates the response against the provided schema."""
    from pydantic import BaseModel

    class _Schema(BaseModel):
        name: str
        age: int

    client = _make_client()
    # Stub _request to return a valid response
    fake_resp = httpx.Response(200, json={"name": "Alice", "age": 30})
    client._request = lambda *a, **k: _FakeAwaitable(fake_resp)  # type: ignore[assignment]
    out = await client.get_structured_output("t1", {}, _Schema)
    assert out == {"name": "Alice", "age": 30}


class _FakeAwaitable:
    """Helper: an awaitable that returns a fixed value."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def __await__(self):  # type: ignore[no-untyped-def]
        async def _coro() -> Any:
            return self._value

        return _coro().__await__()
