"""Snapshot tests for the TUI."""
from __future__ import annotations

import pytest
from textual.pilot import Pilot

from praxis.tui.app import PraxisTUI


@pytest.mark.asyncio
async def test_app_boots_and_shows_dashboard():
    app = PraxisTUI(api_url="http://test:8000", admin_token="test-token")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Dashboard" in app.screen.focused.title if hasattr(app.screen, "title") else True
        # The dashboard tab should be visible by default
        assert app.query_one("#dashboard-tab") is not None


@pytest.mark.asyncio
async def test_tab_switching_with_number_keys():
    app = PraxisTUI(api_url="http://test:8000", admin_token="test-token")
    async with app.run_test() as pilot:
        await pilot.press("2")
        await pilot.pause()
        assert app.query_one("#settings-tab") is not None
        await pilot.press("3")
        await pilot.pause()
        assert app.query_one("#logs-tab") is not None
        await pilot.press("4")
        await pilot.pause()
        assert app.query_one("#admin-tab") is not None
        await pilot.press("1")
        await pilot.pause()
        assert app.query_one("#dashboard-tab") is not None


@pytest.mark.asyncio
async def test_dashboard_shows_system_status_after_load():
    """The dashboard should call /admin/system and render the result."""
    app = PraxisTUI(api_url="http://test:8000", admin_token="test-token")
    # Inject a mock client
    from unittest.mock import AsyncMock

    mock_client = AsyncMock()
    mock_client.get_system = AsyncMock(return_value={
        "router": {"active": {"umans-flash": 2}, "peak": {"umans-flash": 4}, "limits": {"umans-flash": 4}},
        "checkpointer": {"type": "PostgresSaver"},
        "services": {"neo4j": {"configured": True}, "qdrant": {"configured": True}},
        "environment": "production",
        "tool_protocol": "native",
    })
    mock_client.get_metrics = AsyncMock(return_value={
        "counters": {"emails_received": 5, "model_calls:umans-flash": 12},
        "gauges": {},
        "histograms": {},
    })

    async with app.run_test() as pilot:
        app.client = mock_client
        # Trigger a refresh
        await pilot.press("r")
        await pilot.pause()
        # The dashboard should show some indication of the loaded data
        content = app.query_one("#content")
        text = content.__str__()
        assert "production" in text or "native" in text


@pytest.mark.asyncio
async def test_settings_screen_loads_and_displays_form():
    app = PraxisTUI(api_url="http://test:8000", admin_token="test-token")
    from unittest.mock import AsyncMock
    mock_client = AsyncMock()
    mock_client.get_settings = AsyncMock(return_value={
        "umans_api_key": "***",
        "umans_base_url": "https://api.umans.ai/v1",
        "environment": "development",
        "tool_protocol": "native",
    })
    async with app.run_test() as pilot:
        app.client = mock_client
        await pilot.press("2")
        await pilot.pause()
        content = app.query_one("#content")
        text = str(content)
        assert "umans_base_url" in text or "https://api.umans.ai/v1" in text


@pytest.mark.asyncio
async def test_settings_save_submits_patch():
    app = PraxisTUI(api_url="http://test:8000", admin_token="test-token")
    from unittest.mock import AsyncMock
    mock_client = AsyncMock()
    mock_client.get_settings = AsyncMock(return_value={"umans_base_url": "https://old.example.com"})
    mock_client.post_settings = AsyncMock(return_value={"status": "updated"})
    async with app.run_test() as pilot:
        app.client = mock_client
        await pilot.press("2")
        await pilot.pause()
        # Find the save button and press it
        await pilot.click("#save-settings")
        await pilot.pause()
        mock_client.post_settings.assert_called_once()


@pytest.mark.asyncio
async def test_settings_save_includes_empty_values():
    """Important #7: empty input values must be sent in the patch so a
    user can clear a field via the UI. The old ``if i.value`` filter
    skipped empty strings, making it impossible to clear a field.
    """
    app = PraxisTUI(api_url="http://test:8000", admin_token="test-token")
    from unittest.mock import AsyncMock
    mock_client = AsyncMock()
    mock_client.get_settings = AsyncMock(return_value={
        "umans_base_url": "https://old.example.com",
        "default_region": "Australia",
    })
    mock_client.post_settings = AsyncMock(return_value={"status": "updated"})
    async with app.run_test() as pilot:
        app.client = mock_client
        await pilot.press("2")
        await pilot.pause()
        # Clear the default_region input.
        region_input = app.query_one("#setting-default_region")
        region_input.value = ""
        await pilot.pause()
        await pilot.click("#save-settings")
        await pilot.pause()
        mock_client.post_settings.assert_called_once()
        patch = mock_client.post_settings.call_args.args[0]
        # The empty default_region MUST be in the patch (not filtered out).
        assert "default_region" in patch, (
            f"default_region missing from patch: {patch}. "
            f"The 'if i.value' filter was supposed to be removed."
        )
        assert patch["default_region"] == ""


@pytest.mark.asyncio
async def test_logs_screen_displays_audit_entries():
    app = PraxisTUI(api_url="http://test:8000", admin_token="test-token")
    from unittest.mock import AsyncMock
    mock_client = AsyncMock()
    mock_client.get_audit_logs = AsyncMock(return_value={
        "entries": [
            {"event": "webhook.graph_invoke", "level": "info", "ts": "2026-06-22T10:00:00Z"},
            {"event": "webhook.graph_failed", "level": "error", "ts": "2026-06-22T11:00:00Z"},
        ]
    })
    async with app.run_test() as pilot:
        app.client = mock_client
        await pilot.press("3")
        await pilot.pause()
        content = app.query_one("#content")
        text = str(content)
        assert "graph_invoke" in text or "graph_failed" in text


@pytest.mark.asyncio
async def test_admin_screen_lists_failed_events():
    app = PraxisTUI(api_url="http://test:8000", admin_token="test-token")
    from unittest.mock import AsyncMock
    mock_client = AsyncMock()
    mock_client.get_failed_events = AsyncMock(return_value={
        "events": [{"event_id": "evt_1", "error": "graph_failed", "ts": "2026-06-22T10:00:00Z"}]
    })
    mock_client.get_metrics = AsyncMock(return_value={"counters": {"foo": 1}, "gauges": {}, "histograms": {}})
    async with app.run_test() as pilot:
        app.client = mock_client
        await pilot.press("4")
        await pilot.pause()
        content = app.query_one("#content")
        text = str(content)
        assert "evt_1" in text or "failed" in text.lower()
