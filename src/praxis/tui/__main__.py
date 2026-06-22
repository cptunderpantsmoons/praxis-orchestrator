"""CLI entrypoint for `praxis tui`."""
from __future__ import annotations

import os

import click


@click.command()
@click.option("--url", default=None, help="PRAXIS API URL (default: http://localhost:8000)")
@click.option("--token", default=None, help="Admin bearer token")
def main(url: str | None, token: str | None) -> None:
    """Launch the PRAXIS TUI."""
    url = url or os.environ.get("PRAXIS_API_URL", "http://localhost:8000")
    token = token or os.environ.get("PRAXIS_ADMIN_TOKEN", "")
    if not token:
        click.echo("Error: PRAXIS_ADMIN_TOKEN not set. Pass --token or set the env var.", err=True)
        raise SystemExit(1)

    from praxis.tui.app import PraxisTUI

    app = PraxisTUI(api_url=url, admin_token=token)
    app.run()


if __name__ == "__main__":
    main()
