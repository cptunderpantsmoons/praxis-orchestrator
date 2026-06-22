"""PRAXIS TUI — main app shell with tabbed screens."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header, Tab, Tabs

from praxis.tui.api import PraxisClient
from praxis.tui.screens.dashboard import DashboardScreen
from praxis.tui.screens.logs import LogsScreen
from praxis.tui.screens.settings import SettingsScreen

# Maps each tab ID to its human-readable title. Used to label the content
# panel so the focused widget exposes a ``title`` (Textual 8.x stock widgets
# like ``Tabs``/``Tab`` do not have a ``.title`` attribute, but the app's
# tests and downstream screens expect one on the focused element).
_TAB_TITLES: dict[str, str] = {
    "dashboard-tab": "Dashboard",
    "settings-tab": "Settings",
    "logs-tab": "Logs",
    "admin-tab": "Admin",
}

# Maps each tab ID to the DOM id of the screen widget that should be
# visible when that tab is active. C5 wires Dashboard and Settings; C6
# adds Logs. C8 will extend this with Admin once that screen lands.
_TAB_TO_SCREEN: dict[str, str] = {
    "dashboard-tab": "dashboard-screen",
    "settings-tab": "settings-screen",
    "logs-tab": "logs-screen",
}


class ContentPanel(Container):
    """Scrollable content area for the active tab.

    Exposes a ``title`` property mirroring the active tab's label so that the
    focused widget has a meaningful title (Textual 8.x stock widgets lack a
    plain ``.title`` attribute). Screens (C4-C7) mount their content inside
    this panel.

    ``__str__`` walks the widget tree and concatenates the ``content`` of any
    descendant ``Static``/``Label`` so tests can assert on the rendered text
    without going through Textual's full renderer (``Container.__str__``
    otherwise just returns ``"ContentPanel(id='content')"``).
    """

    can_focus = True

    def __init__(self, *, title: str = "Dashboard", **kwargs) -> None:
        super().__init__(**kwargs)
        self._title = title

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        self._title = value

    def __str__(self) -> str:
        parts: list[str] = []
        for node in self.walk_children():
            content = getattr(node, "content", None)
            if isinstance(content, str) and content:
                parts.append(content)
        return "\n".join(parts) if parts else super().__str__()


class PraxisTUI(App):
    """Terminal UI for PRAXIS ops."""

    CSS = """
    Screen {
        layout: vertical;
    }
    #tabs {
        height: 3;
    }
    #content {
        height: 1fr;
    }
    #settings-screen {
        display: none;
    }
    #logs-screen {
        display: none;
    }
    """

    BINDINGS = [
        Binding("1", "switch_tab('dashboard-tab')", "Dashboard", show=True),
        Binding("2", "switch_tab('settings-tab')", "Settings", show=True),
        Binding("3", "switch_tab('logs-tab')", "Logs", show=True),
        Binding("4", "switch_tab('admin-tab')", "Admin", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=True),
    ]

    def __init__(self, api_url: str, admin_token: str) -> None:
        super().__init__()
        self.api_url = api_url
        self.admin_token = admin_token
        self.client: PraxisClient | None = None

    async def on_mount(self) -> None:
        self.client = PraxisClient(self.api_url, self.admin_token)
        # Focus the content panel so the focused widget exposes a ``title``
        # reflecting the active tab.
        self.query_one("#content", ContentPanel).focus()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(
            Tab("Dashboard", id="dashboard-tab"),
            Tab("Settings", id="settings-tab"),
            Tab("Logs", id="logs-tab"),
            Tab("Admin", id="admin-tab"),
            id="tabs",
        )
        # The content panel hosts the screen for whichever tab is active.
        # Dashboard, Settings and Logs exist as of C6; C7 will add Admin.
        # Each screen's ``display`` style is toggled in ``action_switch_tab``
        # based on ``tabs.active``.
        with ContentPanel(id="content", title="Dashboard"):
            yield DashboardScreen(id="dashboard-screen")
            yield SettingsScreen(id="settings-screen")
            yield LogsScreen(id="logs-screen")
        yield Footer()

    def action_switch_tab(self, tab_id: str) -> None:
        """Activate the tab with the given ID.

        Textual 8.x ``Tabs.active`` is a ``reactive[str]`` holding the active
        tab's ID (e.g. ``"dashboard-tab"``), not an integer index. The
        BINDINGS above pass the string tab IDs directly.

        Each tab maps to a screen mounted inside ``#content``; we toggle
        ``display`` so only the active screen is visible. Switching to a
        tab also (re)loads its data so the screen reflects the current
        client — this matters for tests that inject a mock client after
        ``run_test()`` starts, since each screen's ``on_mount`` ran before
        the injection. C8 will generalize this into a per-tab lookup table
        once Logs and Admin screens land.
        """
        tabs = self.query_one(Tabs)
        tabs.active = tab_id
        # Keep the content panel's title in sync with the active tab.
        content = self.query_one("#content", ContentPanel)
        content.title = _TAB_TITLES.get(tab_id, "")
        # Toggle screen visibility based on the active tab.
        screen_id = _TAB_TO_SCREEN.get(tab_id)
        for sid in _TAB_TO_SCREEN.values():
            widget = self.query_one(f"#{sid}")
            widget.styles.display = "block" if sid == screen_id else "none"
        # (Re)load the now-active screen's data so it reflects the current
        # client. The dashboard auto-refreshes on an interval, but the
        # settings and logs screens load on tab switch / explicit refresh.
        if tab_id in ("settings-tab", "logs-tab"):
            self.call_later(self._refresh_active_screen)

    def action_refresh(self) -> None:
        # Each screen handles its own refresh; dispatch to the active screen.
        self.call_later(self._refresh_active_screen)

    async def _refresh_active_screen(self) -> None:
        """Dispatch a refresh to whichever screen is currently active.

        C4 hardcoded the Dashboard; C5 adds Settings; C6 adds Logs. C8
        will generalize this into a per-tab lookup table once the Admin
        screen lands.
        """
        tabs = self.query_one(Tabs)
        if tabs.active == "settings-tab":
            screen = self.query_one("#settings-screen", SettingsScreen)
            await screen.load_settings()
        elif tabs.active == "logs-tab":
            screen = self.query_one("#logs-screen", LogsScreen)
            await screen.load_logs()
        else:
            screen = self.query_one("#dashboard-screen", DashboardScreen)
            await screen.refresh_data()
