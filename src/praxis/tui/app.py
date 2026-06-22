"""PRAXIS TUI — main app shell with tabbed screens."""
from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header, Tab, Tabs

from praxis.tui.api import PraxisClient
from praxis.tui.screens.dashboard import DashboardScreen

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
        # Only the Dashboard screen exists in C4; C5-C7 will add Settings,
        # Logs, and Admin screens and toggle visibility based on
        # ``tabs.active``.
        with ContentPanel(id="content", title="Dashboard"):
            yield DashboardScreen(id="dashboard-screen")
        yield Footer()

    def action_switch_tab(self, tab_id: str) -> None:
        """Activate the tab with the given ID.

        Textual 8.x ``Tabs.active`` is a ``reactive[str]`` holding the active
        tab's ID (e.g. ``"dashboard-tab"``), not an integer index. The
        BINDINGS above pass the string tab IDs directly.
        """
        tabs = self.query_one(Tabs)
        tabs.active = tab_id
        # Keep the content panel's title in sync with the active tab.
        content = self.query_one("#content", ContentPanel)
        content.title = _TAB_TITLES.get(tab_id, "")

    def action_refresh(self) -> None:
        # Each screen handles its own refresh; dispatch to the active screen.
        self.call_later(self._refresh_active_screen)

    async def _refresh_active_screen(self) -> None:
        """Dispatch a refresh to whichever screen is currently active.

        Only the Dashboard screen exists in C4, so we dispatch directly. C8
        will generalize this into a per-tab lookup table once Settings,
        Logs, and Admin screens land.
        """
        screen = self.query_one("#dashboard-screen", DashboardScreen)
        await screen.refresh_data()
