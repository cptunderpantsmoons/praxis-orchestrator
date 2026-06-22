"""Tests for the structlog configuration that populates the audit log
and SSE tail ring buffer.

Important #3 & #4 from the v0.2.0 whole-branch review:

- ``push_log_line`` was defined but never called — the SSE tail endpoint
  streamed from an in-memory ring buffer that nothing populated.
- Nothing wrote to ``logs/audit_log.jsonl`` — the audit log reader
  returned empty forever.

Both are fixed by a structlog processor chain wired in ``main.py``
lifespan via :func:`configure_structlog`.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog

from praxis.config import reset_settings


@pytest.fixture(autouse=True)
def _reset():
    reset_settings()
    yield
    reset_settings()


def test_audit_log_processor_appends_jsonl(tmp_path, monkeypatch):
    """The audit log processor appends a JSON line to the audit log file."""
    from praxis.logging_config import WriteAuditLogProcessor

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(
        "praxis.logging_config._audit_log_path", lambda: log_path
    )

    processor = WriteAuditLogProcessor()
    event_dict = {
        "event": "webhook.graph_invoke",
        "level": "info",
        "ts": "2026-06-22T10:00:00Z",
        "request_id": "req_123",
    }
    result = processor(None, None, event_dict.copy())

    # Processor must return the event_dict unchanged so the chain continues.
    assert result == event_dict
    # File must exist and contain one JSON line with the expected fields.
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "webhook.graph_invoke"
    assert entry["level"] == "info"
    assert entry["ts"] == "2026-06-22T10:00:00Z"


def test_audit_log_processor_preserves_extra_fields(tmp_path, monkeypatch):
    """Extra fields in the event_dict are written to the audit log."""
    from praxis.logging_config import WriteAuditLogProcessor

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(
        "praxis.logging_config._audit_log_path", lambda: log_path
    )

    processor = WriteAuditLogProcessor()
    event_dict = {
        "event": "react.loop_complete",
        "level": "info",
        "ts": "2026-06-22T10:00:00Z",
        "tools_used": ["reply_email"],
        "final_response": "Done.",
    }
    processor(None, None, event_dict.copy())

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tools_used"] == ["reply_email"]
    assert entry["final_response"] == "Done."


def test_sse_tail_processor_pushes_to_ring_buffer(tmp_path, monkeypatch):
    """The SSE tail processor calls push_log_line so the /tail endpoint
    has data to stream.
    """
    from praxis.logging_config import SSETailProcessor
    from praxis.webhooks import logs as logs_module

    # Clear the ring buffer before the test.
    logs_module._RECENT_LINES.clear()
    logs_module._PENDING.clear()

    processor = SSETailProcessor()
    event_dict = {
        "event": "test.event",
        "level": "info",
        "ts": "2026-06-22T10:00:00Z",
    }
    processor(None, None, event_dict.copy())

    # The line must be in the ring buffer.
    assert len(logs_module._RECENT_LINES) == 1
    line = logs_module._RECENT_LINES[0]
    parsed = json.loads(line)
    assert parsed["event"] == "test.event"
    assert parsed["level"] == "info"


def test_configure_structlog_wires_processors(monkeypatch):
    """configure_structlog must set up a processor chain that includes
    both WriteAuditLogProcessor and SSETailProcessor."""
    from praxis.logging_config import configure_structlog

    captured_processors: list = []

    def fake_configure(processors, *args, **kwargs):
        captured_processors.extend(processors)

    monkeypatch.setattr(structlog, "configure", fake_configure)
    configure_structlog()

    # The processor chain must include our two processors.
    processor_names = [type(p).__name__ for p in captured_processors]
    assert "WriteAuditLogProcessor" in processor_names, (
        f"WriteAuditLogProcessor not in chain: {processor_names}"
    )
    assert "SSETailProcessor" in processor_names, (
        f"SSETailProcessor not in chain: {processor_names}"
    )


def test_audit_log_processor_handles_write_failure_gracefully(tmp_path, monkeypatch):
    """If the audit log file can't be written (e.g. disk full), the
    processor must NOT raise — it should silently drop the entry so the
    log chain continues.
    """
    from praxis.logging_config import WriteAuditLogProcessor

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(
        "praxis.logging_config._audit_log_path", lambda: log_path
    )

    processor = WriteAuditLogProcessor()

    # Make the write fail by patching Path.open to raise OSError.
    def fail_open(*args, **kwargs):
        raise OSError("simulated disk full")

    monkeypatch.setattr(Path, "open", fail_open)

    event_dict = {
        "event": "test.event",
        "level": "info",
        "ts": "2026-06-22T10:00:00Z",
    }
    # Must not raise.
    result = processor(None, None, event_dict.copy())
    assert result == event_dict
