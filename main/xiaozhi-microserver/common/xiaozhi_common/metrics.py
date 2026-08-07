"""Shared Prometheus metrics for microserver (phase-6)."""

from __future__ import annotations

from typing import Optional

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        generate_latest,
    )

    REGISTRY = CollectorRegistry()
    WS_ACTIVE = Gauge(
        "xiaozhi_ws_active_connections",
        "Active WebSocket connections",
        registry=REGISTRY,
    )
    WS_MAX = Gauge(
        "xiaozhi_ws_max_connections",
        "Configured max WebSocket connections",
        registry=REGISTRY,
    )
    WS_REJECTED = Counter(
        "xiaozhi_ws_rejected_total",
        "WebSocket connections rejected by limits",
        registry=REGISTRY,
    )
    UPSTREAM = Counter(
        "xiaozhi_upstream_calls_total",
        "Upstream / inter-service calls",
        ["stage", "provider", "status"],
        registry=REGISTRY,
    )
    CIRCUIT = Gauge(
        "xiaozhi_circuit_state",
        "Circuit breaker state (0=closed, 1=half_open, 2=open)",
        ["name"],
        registry=REGISTRY,
    )
    DEGRADED = Counter(
        "xiaozhi_degraded_total",
        "Degradation events",
        ["stage", "kind"],
        registry=REGISTRY,
    )
    METRICS_AVAILABLE = True
except Exception:  # noqa: BLE001
    METRICS_AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    REGISTRY = None
    WS_ACTIVE = None
    WS_MAX = None
    WS_REJECTED = None
    UPSTREAM = None
    CIRCUIT = None
    DEGRADED = None


_STATE_MAP = {"closed": 0, "half_open": 1, "open": 2}


def set_ws_gauges(
    active: Optional[int] = None,
    max_conn: Optional[int] = None,
) -> None:
    if active is not None and WS_ACTIVE is not None:
        WS_ACTIVE.set(active)
    if max_conn is not None and WS_MAX is not None:
        WS_MAX.set(max_conn)


def observe_ws_rejected() -> None:
    if WS_REJECTED is not None:
        WS_REJECTED.inc()


def observe_upstream(stage: str, provider: str, status: str) -> None:
    if UPSTREAM is not None:
        UPSTREAM.labels(stage=stage, provider=provider, status=status).inc()


def set_circuit_state(name: str, state: str) -> None:
    if CIRCUIT is not None:
        CIRCUIT.labels(name=name).set(_STATE_MAP.get(state, 0))


def observe_degraded(stage: str, kind: str) -> None:
    if DEGRADED is not None:
        DEGRADED.labels(stage=stage, kind=kind).inc()


def render_latest() -> bytes:
    if not METRICS_AVAILABLE or REGISTRY is None:
        return b"# prometheus_client not installed\n"
    return generate_latest(REGISTRY)
