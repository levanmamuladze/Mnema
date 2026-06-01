"""
Lightweight, dependency-free metrics in Prometheus text exposition format.

Why hand-rolled instead of prometheus_client: this keeps the runtime free of
an extra dependency for a handful of counters, and the exposition format is
trivial. If you later need histograms/quantiles or a richer ecosystem, swap
this module for prometheus_client behind the same `/metrics` endpoint.

Counters are plain ints mutated from the (single-threaded) asyncio event loop;
increments happen between awaits, so no lock is needed for one Uvicorn worker.
If you move to multiple workers, scrape each worker separately or move to a
shared store — the same caveat that applies to the in-memory rate limiter.
"""
from __future__ import annotations

import threading
from collections import defaultdict

# A lock keeps this correct even if a future change introduces threads
# (e.g. a thread-pool executor incrementing a counter).
_lock = threading.Lock()

# label-tuple -> count
_http_requests: dict[tuple[str, str], int] = defaultdict(int)
_http_duration_sum: dict[tuple[str, str], float] = defaultdict(float)

_counters: dict[str, int] = defaultdict(int)
_gauges: dict[str, int] = defaultdict(int)


def record_request(method: str, status: int, duration_seconds: float) -> None:
    key = (method.upper(), str(status))
    with _lock:
        _http_requests[key] += 1
        _http_duration_sum[key] += duration_seconds


def inc(name: str, amount: int = 1) -> None:
    with _lock:
        _counters[name] += amount


def set_gauge(name: str, value: int) -> None:
    with _lock:
        _gauges[name] = value


def inc_gauge(name: str, amount: int = 1) -> None:
    with _lock:
        _gauges[name] += amount


def dec_gauge(name: str, amount: int = 1) -> None:
    with _lock:
        _gauges[name] -= amount


def _escape(label_value: str) -> str:
    return label_value.replace("\\", "\\\\").replace('"', '\\"')


def render() -> str:
    """Serialise current metrics into Prometheus text exposition format."""
    lines: list[str] = []

    with _lock:
        http_requests = dict(_http_requests)
        http_duration = dict(_http_duration_sum)
        counters = dict(_counters)
        gauges = dict(_gauges)

    lines.append("# HELP mnema_http_requests_total Total HTTP requests.")
    lines.append("# TYPE mnema_http_requests_total counter")
    for (method, status), count in sorted(http_requests.items()):
        lines.append(
            f'mnema_http_requests_total{{method="{_escape(method)}",'
            f'status="{_escape(status)}"}} {count}'
        )

    lines.append(
        "# HELP mnema_http_request_duration_seconds_sum "
        "Cumulative request duration in seconds."
    )
    lines.append("# TYPE mnema_http_request_duration_seconds_sum counter")
    for (method, status), total in sorted(http_duration.items()):
        lines.append(
            f'mnema_http_request_duration_seconds_sum{{method="{_escape(method)}",'
            f'status="{_escape(status)}"}} {total:.6f}'
        )

    for name, value in sorted(counters.items()):
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {value}")

    for name, value in sorted(gauges.items()):
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")

    return "\n".join(lines) + "\n"


def reset() -> None:
    """Clear all metrics — test helper only."""
    with _lock:
        _http_requests.clear()
        _http_duration_sum.clear()
        _counters.clear()
        _gauges.clear()
