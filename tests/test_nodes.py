"""Tests for _build_correction_section.

Covers the P3-F correction ranking algorithm.
"""

from __future__ import annotations

from datetime import UTC, datetime

from praxis.graph.nodes import _build_correction_section
from praxis.models.schemas import CorrectionSummary


def test_empty_corrections() -> None:
    """Returns empty string when no corrections provided."""
    result = _build_correction_section([], "test@example.com")
    assert result == ""


def test_exact_sender_match() -> None:
    """Exact sender match gets highest rank."""
    corrections = [{
        "sender": "alice@example.com",
        "original_triage": {"intent": "SPAM"},
        "confidence": 0.9,
        "timestamp": datetime.now(UTC).isoformat(),
    }]
    result = _build_correction_section(corrections, "alice@example.com")
    assert "LEARNED CORRECTIONS" in result
    assert "alice@example.com" in result


def test_domain_match_ranks_higher() -> None:
    """Domain match (0.7) ranks above fuzzy (0.4)."""
    corrections = [
        {"sender": "bob@other.com", "original_triage": {"intent": "X"}, "confidence": 0.8},
        {"sender": "carol@example.com", "original_triage": {"intent": "Y"}, "confidence": 0.9,
         "timestamp": datetime.now(UTC).isoformat()},
    ]
    result = _build_correction_section(corrections, "dave@example.com")
    assert result.index("example.com") < result.index("other.com")


def test_returns_top_3_only() -> None:
    """Returns at most 3 corrections regardless of input size."""
    corrections = [
        {"sender": f"user{i}@example.com", "original_triage": {"intent": "Z"},
         "confidence": 0.9, "timestamp": datetime.now(UTC).isoformat()}
        for i in range(10)
    ]
    result = _build_correction_section(corrections, "x@example.com")
    sender_lines = [line for line in result.split("\n") if "Sender:" in line]
    assert len(sender_lines) <= 3


def test_truncates_when_too_long() -> None:
    """With enough corrections, only top 3 are included."""
    # 20 corrections with large senders should still be capped
    corrections = [
        {"sender": "a" * 200 + "@example.com", "original_triage": {"intent": "Z"},
         "confidence": 0.9, "timestamp": datetime.now(UTC).isoformat()}
        for _ in range(20)
    ]
    result = _build_correction_section(corrections, "x@example.com")
    # Top 3 logic should limit output
    sender_lines = [line for line in result.split("\n") if "Sender:" in line]
    assert len(sender_lines) <= 3


def test_with_correction_summary_objects() -> None:
    """Handles CorrectionSummary objects (model_dump available)."""
    corrections = [CorrectionSummary(
        correction_id="c1", category="intent", rule="user said spam",
        confidence=0.95, applied_count=1,
    )]
    result = _build_correction_section(corrections, "test@example.com")
    assert "[CORRECTION]" in result


def test_missing_timestamp_falls_back() -> None:
    """Handles corrections without timestamp."""
    corrections = [{"sender": "old@example.com", "original_triage": {"intent": "A"}, "confidence": 0.8}]
    result = _build_correction_section(corrections, "old@example.com")
    assert "old@example.com" in result
