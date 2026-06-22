"""Admin screen — failed events table + retry actions."""
from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Label, Static

from praxis.tui.api import PraxisClient


class AdminScreen(Static):
    """Failed-events table with retry actions.

    Renders the most recent failed events (``GET /admin/failed-events``) into
    a ``DataTable`` widget and exposes three actions: retry the currently
    selected row, retry every row, and refresh. The ``Retry Selected`` and
    ``Retry All`` buttons POST the event ID(s) to ``/admin/retry-failed``.

    Reads its HTTP client from ``self.app.client`` (matching
    ``DashboardScreen`` / ``SettingsScreen`` / ``LogsScreen``) so tests can
    inject a mock client on the app after ``run_test()`` starts.

    ``ContentPanel.__str__`` walks descendants and concatenates the
    ``content`` of any ``Static``/``Label``. ``DataTable`` is not a
    ``Static`` (it stores cells keyed by RowKey/ColumnKey, not a
    ``.content`` string), so its rows would be invisible to
    ``str(content)``. To keep tests that assert on ``str(content)``
    working, we also keep a hidden ``Static`` (``#failed-events-mirror``)
    whose ``content`` mirrors the table rows as plain text. The
    ``DataTable`` remains the user-facing widget (scrollable, sortable);
    the mirror is purely for the test-facing ``__str__`` walker.
    """

    DEFAULT_CSS = """
    AdminScreen {
        layout: vertical;
        padding: 0 1;
    }
    #admin-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #failed-events-title {
        margin-bottom: 1;
    }
    #failed-events-table {
        height: 1fr;
        margin-bottom: 1;
    }
    #admin-actions {
        height: 3;
        margin-bottom: 1;
    }
    #admin-actions Button {
        margin-right: 1;
    }
    #failed-events-mirror {
        display: none;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    @property
    def client(self) -> PraxisClient:
        # Always read the app's current client — this is what tests override.
        return self.app.client  # type: ignore[attr-defined]

    def compose(self):
        yield Label("Admin", id="admin-title")
        yield Label("Failed Events", id="failed-events-title")
        yield Vertical(
            DataTable(id="failed-events-table"),
            Horizontal(
                Button("Retry Selected", id="retry-selected", variant="warning"),
                Button("Retry All", id="retry-all", variant="error"),
                Button("Refresh", id="refresh-admin", variant="default"),
                id="admin-actions",
            ),
            Label("", id="admin-status"),
            # Hidden mirror so ``ContentPanel.__str__`` can see the rendered
            # table rows (DataTable stores cells keyed by RowKey/ColumnKey,
            # not a ``.content`` string).
            Static("", id="failed-events-mirror"),
        )

    async def on_mount(self) -> None:
        table = self.query_one("#failed-events-table", DataTable)
        table.add_columns("Event ID", "Error", "Timestamp")
        await self.load_failed_events()

    async def load_failed_events(self) -> None:
        try:
            data = await self.client.get_failed_events()
        except Exception as exc:
            self.query_one("#admin-status", Label).update(f"[red]Error: {exc}[/]")
            self.query_one("#failed-events-mirror", Static).update(f"Error: {exc}")
            return
        table = self.query_one("#failed-events-table", DataTable)
        table.clear()
        mirror_lines: list[str] = []
        events = data.get("events", []) if isinstance(data, dict) else []
        for event in events:
            event_id = event.get("event_id", "")
            error = event.get("error", "")
            ts = event.get("ts", "")
            table.add_row(event_id, error, ts)
            # Mirror without markup so the test-facing string is plain text.
            mirror_lines.append(f"{event_id} {error} {ts}".rstrip())
        self.query_one("#failed-events-mirror", Static).update(
            "\n".join(mirror_lines)
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "retry-selected":
            await self._retry_selected()
        elif event.button.id == "retry-all":
            await self._retry_all()
        elif event.button.id == "refresh-admin":
            await self.load_failed_events()

    async def _retry_selected(self) -> None:
        table = self.query_one("#failed-events-table", DataTable)
        if table.row_count == 0:
            self.query_one("#admin-status", Label).update(
                "[yellow]No events to retry.[/]"
            )
            return
        row = table.get_row_at(table.cursor_row)
        event_id = str(row[0]) if row else ""
        try:
            await self.client.retry_failed(event_id)
            self.query_one("#admin-status", Label).update(
                f"[green]Retried {event_id}[/]"
            )
            await self.load_failed_events()
        except Exception as exc:
            self.query_one("#admin-status", Label).update(
                f"[red]Retry failed: {exc}[/]"
            )

    async def _retry_all(self) -> None:
        table = self.query_one("#failed-events-table", DataTable)
        for index in range(table.row_count):
            row = table.get_row_at(index)
            event_id = str(row[0]) if row else ""
            try:
                await self.client.retry_failed(event_id)
            except Exception as exc:
                self.query_one("#admin-status", Label).update(
                    f"[red]Retry failed for {event_id}: {exc}[/]"
                )
        await self.load_failed_events()
