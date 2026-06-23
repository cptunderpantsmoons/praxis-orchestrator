"""In-process metrics registry — counters, histograms, gauges.

Not thread-safe by default; callers must hold an asyncio.Lock for cross-coroutine
writes. For single-process FastAPI deployment, the GIL + asyncio's cooperative
scheduling is sufficient.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

# Maps the base metric name (the part before ':') to the Prometheus label name.
# Metrics like `tool_failures:reply_email` use `tool` as the label, while
# `recovery_attempts:tool_returned_empty` use `pattern`. Defaults to "model"
# for backward compatibility with the original model_* metrics.
_LABEL_FOR_BASE: dict[str, str] = {
    "model_calls": "model",
    "model_latency_ms": "model",
    "router_active": "model",
    "router_peak": "model",
    "tool_failures": "tool",
    "recovery_attempts": "pattern",
    "recovery_successes": "pattern",
    "recovery_failures": "pattern",
    "recovery_escalated": "pattern",
}


class MetricsRegistry:
    """In-memory metrics store with Prometheus text export."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._histograms: dict[str, list[int]] = {}
        self._gauges: dict[str, int] = {}

    def inc_counter(self, name: str, value: int = 1) -> None:
        self._counters[name] = self._counters.get(name, 0) + value

    def observe_histogram(self, name: str, value_ms: int) -> None:
        self._histograms.setdefault(name, []).append(value_ms)

    def set_gauge(self, name: str, value: int) -> None:
        self._gauges[name] = value

    def increment_gauge(self, name: str, delta: int = 1) -> None:
        self._gauges[name] = self._gauges.get(name, 0) + delta

    def decrement_gauge(self, name: str, delta: int = 1) -> None:
        self._gauges[name] = self._gauges.get(name, 0) - delta

    def snapshot(self) -> dict[str, Any]:
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {
                name: {
                    "count": len(values),
                    "sum": sum(values),
                    "min": min(values) if values else 0,
                    "max": max(values) if values else 0,
                }
                for name, values in self._histograms.items()
            },
        }

    def prometheus_text(self) -> str:
        lines: list[str] = []
        for name, value in sorted(self._counters.items()):
            base, _, label = name.partition(":")
            if label:
                label_name = _LABEL_FOR_BASE.get(base, "model")
                lines.append(f"# TYPE {base} counter")
                lines.append(f'{base}{{{label_name}="{label}"}} {value}')
            else:
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {value}")
        for name, value in sorted(self._gauges.items()):
            base, _, label = name.partition(":")
            if label:
                label_name = _LABEL_FOR_BASE.get(base, "model")
                lines.append(f"# TYPE {base} gauge")
                lines.append(f'{base}{{{label_name}="{label}"}} {value}')
            else:
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name} {value}")
        for name, values in sorted(self._histograms.items()):
            base, _, label = name.partition(":")
            lines.append(f"# TYPE {base} histogram")
            count = len(values)
            total = sum(values) if values else 0
            label_name = _LABEL_FOR_BASE.get(base, "model")
            label_str = f'{{{label_name}="{label}"}}' if label else ""
            lines.append(f"{base}_count{label_str} {count}")
            lines.append(f"{base}_sum{label_str} {total}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        self._counters.clear()
        self._histograms.clear()
        self._gauges.clear()


@lru_cache
def get_metrics() -> MetricsRegistry:
    """Return the process-wide metrics singleton."""
    return MetricsRegistry()
