"""Tests for RecoveryQueue."""

import asyncio
from pathlib import Path

import pytest

from praxis.recovery.models import FailureContext, RecoveryEvent
from praxis.recovery.queue import RecoveryQueue


def _make_event(tool_name: str = "dummy_search", error: str = "fail") -> RecoveryEvent:
    return RecoveryEvent(
        failure=FailureContext(
            tool_name=tool_name,
            error_message=error,
            args={},
        )
    )


def test_enqueue_and_len(tmp_path):
    q = RecoveryQueue(max_size=10, persist_path=tmp_path / "q.jsonl")
    assert len(q) == 0
    q.enqueue(_make_event())
    assert len(q) == 1


def test_dequeue_returns_event(tmp_path):
    q = RecoveryQueue(max_size=10, persist_path=tmp_path / "q.jsonl")
    e = _make_event(tool_name="reply_email", error="boom")
    q.enqueue(e)
    got = asyncio.run(q.dequeue(timeout=0.5))
    assert got is not None
    assert got.event_id == e.event_id
    assert len(q) == 0


def test_dequeue_returns_none_on_timeout(tmp_path):
    q = RecoveryQueue(max_size=10, persist_path=tmp_path / "q.jsonl")
    got = asyncio.run(q.dequeue(timeout=0.1))
    assert got is None


def test_enqueue_persists_to_jsonl(tmp_path):
    path = tmp_path / "events.jsonl"
    q = RecoveryQueue(max_size=10, persist_path=path)
    q.enqueue(_make_event(tool_name="list_agents", error="empty"))
    assert path.exists()
    content = path.read_text()
    assert "list_agents" in content
    assert "empty" in content


def test_queue_overflow_drops_oldest(tmp_path):
    q = RecoveryQueue(max_size=2, persist_path=tmp_path / "q.jsonl")
    e1 = _make_event(tool_name="t1", error="e1")
    e2 = _make_event(tool_name="t2", error="e2")
    e3 = _make_event(tool_name="t3", error="e3")
    q.enqueue(e1)
    q.enqueue(e2)
    q.enqueue(e3)
    assert len(q) == 2  # maxlen=2, oldest dropped
    items = list(q._deque)
    assert items[0].failure.tool_name == "t2"
    assert items[1].failure.tool_name == "t3"


def test_snapshot_returns_most_recent(tmp_path):
    q = RecoveryQueue(max_size=10, persist_path=tmp_path / "q.jsonl")
    for i in range(5):
        q.enqueue(_make_event(tool_name=f"t{i}", error=f"e{i}"))
    snap = q.snapshot(limit=3)
    assert len(snap) == 3
    # Most recent first
    assert snap[0].failure.tool_name == "t4"
    assert snap[1].failure.tool_name == "t3"
    assert snap[2].failure.tool_name == "t2"


def test_snapshot_empty(tmp_path):
    q = RecoveryQueue(max_size=10, persist_path=tmp_path / "q.jsonl")
    assert q.snapshot(limit=10) == []


def test_snapshot_zero_limit(tmp_path):
    q = RecoveryQueue(max_size=10, persist_path=tmp_path / "q.jsonl")
    q.enqueue(_make_event())
    assert q.snapshot(limit=0) == []


def test_queue_works_when_persist_dir_not_writable(tmp_path):
    """When the logs directory can't be created (e.g. read-only container fs),
    the queue falls back to in-memory-only mode instead of crashing."""
    read_only = tmp_path / "readonly"
    read_only.mkdir()
    read_only.chmod(0o555)
    q = RecoveryQueue(max_size=10, persist_path=read_only / "subdir" / "q.jsonl")
    assert q._persistence_enabled is False
    q.enqueue(_make_event())
    assert len(q) == 1
    event = asyncio.run(q.dequeue(timeout=0.1))
    assert event is not None
