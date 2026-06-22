"""``praxis tui`` CLI entry point.

Implemented in Task C3; this module exists so the ``[project.scripts]``
entry point in ``pyproject.toml`` resolves.
"""
from __future__ import annotations


def main() -> None:
    """Placeholder entry point — replaced in Task C3."""
    try:
        from praxis.tui.app import run_tui
    except ImportError as exc:
        raise SystemExit(
            "TUI app not yet implemented. Complete Task C3 first."
        ) from exc
    run_tui()


if __name__ == "__main__":
    main()
