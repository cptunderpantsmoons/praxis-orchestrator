"""Logs screen — audit log viewer with filters."""
from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, RichLog, Static

from praxis.tui.api import PraxisClient


class LogsScreen(Static):
    """Audit log viewer.

    Renders the most recent audit log entries (``GET /admin/logs/audit``)
    into a scrollable ``RichLog`` widget and exposes two filters
    (event / level) plus an ``Apply`` button to re-query the backend.

    Reads its HTTP client from ``self.app.client`` (matching
    ``DashboardScreen`` / ``SettingsScreen``) so tests can inject a mock
    client on the app after ``run_test()`` starts.

    ``ContentPanel.__str__`` walks descendants and concatenates the
    ``content`` of any ``Static``/``Label``. ``RichLog`` is not a
    ``Static`` (it stores rendered ``Strip``s, not a ``.content`` string),
    so its text would be invisible to ``str(content)``. To keep tests that
    assert on ``str(content)`` working, we also keep a hidden ``Static``
    (``#audit-log-mirror``) whose ``content`` mirrors the rendered log
    text. The ``RichLog`` remains the user-facing widget (scrollable,
    colored); the mirror is purely for the test-facing ``__str__`` walker.
    """

    DEFAULT_CSS = """
    LogsScreen {
        layout: vertical;
        padding: 0 1;
    }
    #logs-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #log-filters {
        height: 3;
        margin-bottom: 1;
    }
    #log-filters Input {
        width: 1fr;
        margin-right: 1;
    }
    #audit-log {
        height: 1fr;
    }
    #audit-log-mirror {
        display: none;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    @property
    def client(self) -> PraxisClient:
        # Always read the app's current client — this is what tests override.
        return self.app.client  # type: ignore[attr-defined]

    def compose(self) -> None:
        yield Label("Logs", id="logs-title")
        yield Horizontal(
            Input(placeholder="event filter", id="log-event-filter"),
            Input(placeholder="level filter", id="log-level-filter"),
            Button("Apply", id="apply-log-filter", variant="primary"),
            id="log-filters",
        )
        yield RichLog(id="audit-log", highlight=True, markup=True)
        # Hidden mirror so ``ContentPanel.__str__`` can see the rendered
        # log text (RichLog stores Strips, not a ``.content`` string).
        yield Static("", id="audit-log-mirror")

    async def on_mount(self) -> None:
        await self.load_logs()
        self.set_interval(10, self.load_logs)

    async def load_logs(self) -> None:
        event = self.query_one("#log-event-filter", Input).value or None
        level = self.query_one("#log-level-filter", Input).value or None
        try:
            data = await self.client.get_audit_logs(
                event=event, level=level, limit=200
            )
        except Exception as exc:
            msg = f"[red]Error: {exc}[/]"
            self.query_one("#audit-log", RichLog).write(msg)
            self.query_one("#audit-log-mirror", Static).update(
                f"Error: {exc}"
            )
            return
        log = self.query_one("#audit-log", RichLog)
        log.clear()
        mirror_lines: list[str] = []
        entries = data.get("entries", []) if isinstance(data, dict) else []
        for entry in reversed(entries):
            ts = entry.get("ts", "")
            lvl = entry.get("level", "")
            evt = entry.get("event", "")
            color = {
                "error": "red",
                "warning": "yellow",
                "info": "cyan",
            }.get(lvl, "white")
            log.write(f"[{ts}] [{color}]{lvl}[/] {evt}")
            # Mirror without markup so the test-facing string is plain text.
            mirror_lines.append(f"[{ts}] {lvl} {evt}")
        self.query_one("#audit-log-mirror", Static).update(
            "\n".join(mirror_lines)
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-log-filter":
            await self.load_logs()
