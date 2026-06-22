"""Tests for the MetricsRegistry."""
from __future__ import annotations

import pytest

from praxis.features.metrics import MetricsRegistry, get_metrics


@pytest.fixture
def registry():
    return MetricsRegistry()


def test_counter_increment(registry):
    registry.inc_counter("model_calls:umans-flash")
    assert registry.snapshot()["counters"]["model_calls:umans-flash"] == 1
    registry.inc_counter("model_calls:umans-flash", 2)
    assert registry.snapshot()["counters"]["model_calls:umans-flash"] == 3


def test_histogram_records_values(registry):
    registry.observe_histogram("model_latency_ms:umans-flash", 100)
    registry.observe_histogram("model_latency_ms:umans-flash", 200)
    snap = registry.snapshot()["histograms"]["model_latency_ms:umans-flash"]
    assert snap["count"] == 2
    assert snap["sum"] == 300
    assert snap["min"] == 100
    assert snap["max"] == 200


def test_gauge_set_and_increment(registry):
    registry.set_gauge("router_active:umans-flash", 1)
    registry.increment_gauge("router_active:umans-flash")
    assert registry.snapshot()["gauges"]["router_active:umans-flash"] == 2
    registry.decrement_gauge("router_active:umans-flash")
    assert registry.snapshot()["gauges"]["router_active:umans-flash"] == 1


def test_reset_zeros_all(registry):
    registry.inc_counter("foo")
    registry.set_gauge("bar", 5)
    registry.observe_histogram("baz", 10)
    registry.reset()
    snap = registry.snapshot()
    assert snap["counters"] == {}
    assert snap["gauges"] == {}
    assert snap["histograms"] == {}


def test_prometheus_text_format(registry):
    registry.inc_counter("model_calls:umans-flash")
    registry.set_gauge("router_active:umans-flash", 2)
    registry.observe_histogram("model_latency_ms:umans-flash", 50)
    text = registry.prometheus_text()
    assert "# TYPE model_calls counter" in text
    assert "model_calls{model=\"umans-flash\"} 1" in text or "model_calls_umans_flash 1" in text
    assert "# TYPE router_active gauge" in text
    assert "# TYPE model_latency_ms histogram" in text


def test_get_metrics_is_singleton():
    a = get_metrics()
    b = get_metrics()
    assert a is b
