"""Tests for the PraxisClient HTTP wrapper."""
from __future__ import annotations

import httpx
import pytest

from praxis.tui.api import PraxisClient


@pytest.fixture
def client():
    return PraxisClient(base_url="http://test:8000", token="test-token")


@pytest.mark.asyncio
async def test_get_system_sends_bearer_token(client):
    request_received = {}

    def handler(request):
        request_received["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"router": {}, "checkpointer": {}, "services": {}})

    transport = httpx.MockTransport(handler)
    client._http = httpx.AsyncClient(
        transport=transport,
        base_url="http://test:8000",
        headers={"Authorization": "Bearer test-token"},
    )
    result = await client.get_system()
    assert request_received["auth"] == "Bearer test-token"
    assert "router" in result


@pytest.mark.asyncio
async def test_post_settings_sends_patch(client):
    received_body = {}

    def handler(request):
        received_body["json"] = request.read().decode()
        return httpx.Response(200, json={"status": "updated"})

    transport = httpx.MockTransport(handler)
    client._http = httpx.AsyncClient(transport=transport, base_url="http://test:8000")
    await client.post_settings({"umans_base_url": "https://new.example.com"})
    assert "umans_base_url" in received_body["json"]


@pytest.mark.asyncio
async def test_get_metrics_returns_snapshot(client):
    def handler(request):
        return httpx.Response(200, json={"counters": {"foo": 1}, "gauges": {}, "histograms": {}})

    transport = httpx.MockTransport(handler)
    client._http = httpx.AsyncClient(transport=transport, base_url="http://test:8000")
    result = await client.get_metrics()
    assert result["counters"]["foo"] == 1
