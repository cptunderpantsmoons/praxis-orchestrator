"""structlog configuration with audit log and SSE tail processors.

Important #3 & #4 from the v0.2.0 whole-branch review:

- ``push_log_line`` (in ``praxis.webhooks.logs``) was defined but never
  called — the SSE ``/admin/logs/tail`` endpoint streamed from an
  in-memory ring buffer that nothing populated.
- Nothing wrote to ``logs/audit_log.jsonl`` — the
  ``/admin/logs/audit`` reader returned empty forever.

Both are fixed by the structlog processor chain wired here.
:func:`configure_structlog` sets up a chain that includes:

- :class:`WriteAuditLogProcessor` — appends each log entry as a JSON
  line to ``logs/audit_log.jsonl`` (the file the audit reader consumes).
- :class:`SSETailProcessor` — calls ``push_log_line`` so the SSE tail
  endpoint has data to stream to connected clients.

Both processors are defensive: they never raise (write failures are
swallowed) so the log chain always continues. They return the
``event_dict`` unchanged so other processors in the chain (formatters,
renderers) still see the full event.

Call :func:`configure_structlog` once at application startup (in
``main.py`` lifespan) before any logging happens.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import structlog

from praxis.webhooks.logs import push_log_line


def _audit_log_path() -> Path:
    """Return the path to the audit log JSONL file.

    Mirrors ``praxis.webhooks.logs._audit_log_path`` so the writer and
    reader agree on the location. Kept as a separate function (rather
    than importing the one from ``logs.py``) so tests can monkeypatch
    each independently without cross-talk.
    """
    return Path("logs/audit_log.jsonl")


def _ensure_audit_log_dir(path: Path) -> None:
    """Create the parent directory of the audit log if it doesn't exist."""
    parent = path.parent
    if parent and not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Can't create the dir — the write will fail and the
            # processor will swallow the error. Don't raise here.
            pass


class WriteAuditLogProcessor:
    """structlog processor that appends each event as a JSON line to the
    audit log file.

    The processor writes a JSON object with the event's fields
    (``event``, ``level``, ``ts``, and any additional context). It
    never raises — write failures are logged to stderr and swallowed so
    the log chain continues.

    The processor returns the ``event_dict`` unchanged so subsequent
    processors (formatters, renderers) still see the full event.
    """

    def __call__(
        self,
        logger: Any,
        name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            path = _audit_log_path()
            _ensure_audit_log_dir(path)
            # Serialise the event_dict to a JSON line. ``default=str``
            # handles non-JSON-serialisable values (datetime, Path, etc.).
            line = json.dumps(event_dict, default=str)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            # Swallow — the log chain must not break on a write failure.
            # We intentionally don't log via structlog here (would recurse).
            pass
        return event_dict


class SSETailProcessor:
    """structlog processor that pushes each rendered log line to the
    SSE tail ring buffer via ``push_log_line``.

    This populates the in-memory ring buffer that
    ``GET /admin/logs/tail`` streams to connected clients. The processor
    never raises — push failures are swallowed so the log chain
    continues.

    The processor returns the ``event_dict`` unchanged so subsequent
    processors still see the full event.
    """

    def __call__(
        self,
        logger: Any,
        name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            line = json.dumps(event_dict, default=str)
            push_log_line(line)
        except Exception:
            # Swallow — the log chain must not break on a push failure.
            pass
        return event_dict


def configure_structlog() -> None:
    """Configure structlog with the audit log and SSE tail processors.

    Call once at application startup (in ``main.py`` lifespan) before
    any logging happens. Idempotent — safe to call multiple times.

    The processor chain (in order):

    1. ``structlog.contextvars.merge_contextvars`` — merge contextvars
    2. ``structlog.processors.add_log_level`` — add ``level`` field
    3. ``structlog.processors.TimeStamper(fmt="iso")`` — add ``ts`` field
    4. :class:`WriteAuditLogProcessor` — append to ``logs/audit_log.jsonl``
    5. :class:`SSETailProcessor` — push to SSE ring buffer
    6. ``structlog.processors.JSONRenderer()`` — render as JSON for stdout
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            WriteAuditLogProcessor(),
            SSETailProcessor(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


__all__ = [
    "SSETailProcessor",
    "WriteAuditLogProcessor",
    "configure_structlog",
]
