"""Unit tests for the dependency-free Prometheus metrics module."""
from __future__ import annotations

from core import metrics


def test_request_counts_aggregate_by_method_and_status():
    metrics.reset()
    metrics.record_request("get", 200, 0.01)
    metrics.record_request("GET", 200, 0.02)  # case-insensitive method
    metrics.record_request("post", 500, 0.50)

    out = metrics.render()

    assert 'mnema_http_requests_total{method="GET",status="200"} 2' in out
    assert 'mnema_http_requests_total{method="POST",status="500"} 1' in out
    # Duration sum accumulates per label set.
    assert 'mnema_http_request_duration_seconds_sum{method="GET",status="200"} 0.030000' in out


def test_counters_and_gauges_render():
    metrics.reset()
    metrics.inc("mnema_chat_streams_total", 2)
    metrics.inc("mnema_chat_streams_total")  # -> 3
    metrics.set_gauge("mnema_llm_generations_active", 1)
    metrics.inc_gauge("mnema_llm_generations_active")  # -> 2
    metrics.dec_gauge("mnema_llm_generations_active")  # -> 1

    out = metrics.render()

    assert "# TYPE mnema_chat_streams_total counter" in out
    assert "mnema_chat_streams_total 3" in out
    assert "# TYPE mnema_llm_generations_active gauge" in out
    assert "mnema_llm_generations_active 1" in out


def test_render_is_valid_exposition_shape():
    metrics.reset()
    metrics.record_request("GET", 200, 0.0)
    out = metrics.render()
    # Every non-comment line must be `name value` (optionally with labels).
    for line in out.splitlines():
        if not line or line.startswith("#"):
            continue
        assert line.rsplit(" ", 1)[-1]  # has a trailing value token
    assert out.endswith("\n")


def test_reset_clears_everything():
    metrics.inc("x")
    metrics.set_gauge("y", 5)
    metrics.record_request("GET", 200, 1.0)
    metrics.reset()
    assert metrics.render().strip().count("\n") >= 0  # renders without error
    assert "x 1" not in metrics.render()
    assert "y 5" not in metrics.render()
