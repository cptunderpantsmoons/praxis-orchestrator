"""Dashboard screen — system status + metrics."""
from __future__ import annotations

from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Label, Static

from praxis.tui.api import PraxisClient


class DashboardScreen(Static):
    """Live system dashboard.

    Reads its HTTP client from ``self.app.client`` (the app's ``PraxisClient``)
    rather than holding its own copy, so tests can inject a mock client on the
    app after mount and have refreshes pick it up immediately.
    """

    system_status: reactive[dict | None] = reactive(None)
    metrics: reactive[dict | None] = reactive(None)

    DEFAULT_CSS = """
    DashboardScreen {
        layout: vertical;
        padding: 0 1;
    }
    #dashboard-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #dashboard-content {
        layout: vertical;
    }
    """

    @property
    def client(self) -> PraxisClient:
        # Always read the app's current client — this is what tests override.
        return self.app.client  # type: ignore[attr-defined]

    def compose(self) -> None:
        yield Label("PRAXIS Dashboard", id="dashboard-title")
        yield Vertical(
            Label("Loading...", id="system-status"),
            Label("", id="metrics-summary"),
            id="dashboard-content",
        )

    async def on_mount(self) -> None:
        await self.refresh_data()
        self.set_interval(5, self.refresh_data)

    async def refresh_data(self) -> None:
        try:
            self.system_status = await self.client.get_system()
            self.metrics = await self.client.get_metrics()
        except Exception as exc:
            self.query_one("#system-status", Label).update(f"[red]Error: {exc}[/]")

    def watch_system_status(self, status: dict | None) -> None:
        if not status:
            return
        router = status.get("router", {})
        services = status.get("services", {})
        env = status.get("environment", "?")
        protocol = status.get("tool_protocol", "?")

        lines = [f"Environment: [cyan]{env}[/]  |  Protocol: [cyan]{protocol}[/]"]
        lines.append("")
        lines.append("[bold]Router:[/]")
        for model, active in router.get("active", {}).items():
            peak = router.get("peak", {}).get(model, 0)
            limit = router.get("limits", {}).get(model, "?")
            lines.append(f"  {model}: {active}/{limit} active (peak {peak})")
        lines.append("")
        lines.append("[bold]Services:[/]")
        for name, info in services.items():
            if name in ("metrics_counters", "metrics_gauges"):
                continue
            configured = info.get("configured", False) if isinstance(info, dict) else False
            status_str = "[green]configured[/]" if configured else "[red]missing[/]"
            lines.append(f"  {name}: {status_str}")
        self.query_one("#system-status", Label).update("\n".join(lines))

    def watch_metrics(self, metrics: dict | None) -> None:
        if not metrics:
            return
        counters = metrics.get("counters", {})
        lines = ["[bold]Metrics:[/]"]
        for key in ("emails_received", "emails_sent", "emails_failed", "replies_sent_duplicate_blocked"):
            if key in counters:
                lines.append(f"  {key}: {counters[key]}")
        lines.append("")
        for key, value in sorted(counters.items()):
            if key.startswith("model_calls:") or key.startswith("tool_calls:"):
                lines.append(f"  {key}: {value}")
        self.query_one("#metrics-summary", Label).update("\n".join(lines))
