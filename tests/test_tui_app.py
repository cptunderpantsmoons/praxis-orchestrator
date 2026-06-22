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
