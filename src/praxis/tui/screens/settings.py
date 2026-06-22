"""Settings screen — view and edit app settings."""
from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Static

from praxis.tui.api import PraxisClient

# Field names whose values are secrets — rendered with password masking.
SECRET_FIELDS = {
    "umans_api_key",
    "agentmail_api_key",
    "agentmail_webhook_secret",
    "neo4j_password",
    "qdrant_api_key",
    "ldr_api_key",
    "hermes_api_key",
    "praxis_admin_token",
}


class SettingsScreen(Static):
    """Settings editor.

    Loads the current settings from ``GET /admin/settings`` on mount and
    renders one ``Input`` per field (with ``password=True`` for secret
    fields). The ``Save`` button POSTs the edited values back via
    ``POST /admin/settings``; ``Reset`` reloads from the server.

    Reads its HTTP client from ``self.app.client`` (matching
    ``DashboardScreen``) so tests can inject a mock client on the app
    after ``run_test()`` starts.
    """

    DEFAULT_CSS = """
    SettingsScreen {
        layout: vertical;
        padding: 0 1;
    }
    #settings-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #settings-form {
        layout: vertical;
        height: auto;
    }
    #settings-form Label {
        margin-top: 1;
        color: $text-muted;
    }
    #settings-actions {
        height: 3;
        margin-top: 1;
    }
    #settings-actions Button {
        margin-right: 1;
    }
    #settings-status {
        margin-top: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._inputs: dict[str, Input] = {}

    @property
    def client(self) -> PraxisClient:
        # Always read the app's current client — this is what tests override.
        return self.app.client  # type: ignore[attr-defined]

    def compose(self) -> None:
        yield Label("Settings", id="settings-title")
        yield Vertical(id="settings-form")
        yield Horizontal(
            Button("Save", id="save-settings", variant="success"),
            Button("Reset", id="reset-settings", variant="default"),
            id="settings-actions",
        )
        yield Label("", id="settings-status")

    async def on_mount(self) -> None:
        await self.load_settings()

    async def load_settings(self) -> None:
        try:
            data = await self.client.get_settings()
        except Exception as exc:
            self.query_one("#settings-status", Label).update(f"[red]Error: {exc}[/]")
            return
        form = self.query_one("#settings-form")
        form.remove_children()
        self._inputs.clear()
        for key, value in sorted(data.items()):
            password = key in SECRET_FIELDS
            inp = Input(
                value=str(value) if value is not None else "",
                password=password,
                placeholder=key,
                id=f"setting-{key}",
            )
            self._inputs[key] = inp
            # ``mount`` returns an ``AwaitMount`` we can await to guarantee
            # the children are in the DOM before the test inspects them.
            await form.mount(Label(key))
            await form.mount(inp)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-settings":
            await self.save_settings()
        elif event.button.id == "reset-settings":
            await self.load_settings()

    async def save_settings(self) -> None:
        # Important #7: do NOT filter out empty strings. The old
        # ``if i.value`` filter skipped empty values, making it impossible
        # for a user to clear a field via the UI. Send all fields; the
        # server treats "***" as no-change for secrets, and writes empty
        # strings for non-secrets.
        patch = {k: i.value for k, i in self._inputs.items()}
        try:
            result = await self.client.post_settings(patch)
            fields = result.get("fields", []) if isinstance(result, dict) else []
            self.query_one("#settings-status", Label).update(
                f"[green]Saved: {fields}[/]"
            )
        except Exception as exc:
            self.query_one("#settings-status", Label).update(
                f"[red]Save failed: {exc}[/]"
            )
