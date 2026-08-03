"""Prometheus 可观测性指标（第一期）。

通过 HTTP /metrics 暴露，供 Prometheus 拉取。
未安装 prometheus_client 或 metrics.enabled=false 时全部为 no-op。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Optional

_ENABLED = False
_INITIALIZED = False

# 占位，避免 import 失败
ws_active = None
ws_rejected_total = None
ws_sessions_opened_total = None
ws_session_duration_seconds = None
provider_requests_total = None
provider_latency_seconds = None
provider_ttfb_seconds = None
queue_depth = None
ws_max_connections = None
circuit_state = None
degraded_total = None
overload_shed_total = None


def init_metrics(config: Optional[dict] = None) -> bool:
    """根据配置初始化指标。可重复调用（仅首次生效）。"""
    global _ENABLED, _INITIALIZED
    global ws_active, ws_rejected_total, ws_sessions_opened_total
    global ws_session_duration_seconds, provider_requests_total
    global provider_latency_seconds, provider_ttfb_seconds, queue_depth
    global ws_max_connections, circuit_state, degraded_total, overload_shed_total

    if _INITIALIZED:
        return _ENABLED

    _INITIALIZED = True
    server = (config or {}).get("server") or {}
    metrics_cfg = server.get("metrics") or {}
    if not metrics_cfg.get("enabled", True):
        _ENABLED = False
        return False

    try:
        from prometheus_client import Counter, Gauge, Histogram
    except ImportError:
        _ENABLED = False
        return False

    ws_active = Gauge(
        "xiaozhi_ws_active_connections",
        "Current active WebSocket sessions",
    )
    ws_max_connections = Gauge(
        "xiaozhi_ws_max_connections",
        "Configured max WebSocket connections",
    )
    ws_rejected_total = Counter(
        "xiaozhi_ws_rejected_total",
        "WebSocket connections rejected by hard limits",
        ["reason"],
    )
    ws_sessions_opened_total = Counter(
        "xiaozhi_ws_sessions_opened_total",
        "WebSocket sessions successfully accepted",
    )
    ws_session_duration_seconds = Histogram(
        "xiaozhi_ws_session_duration_seconds",
        "WebSocket session lifetime in seconds",
        buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600),
    )
    provider_requests_total = Counter(
        "xiaozhi_provider_requests_total",
        "Upstream provider requests",
        ["component", "provider", "status"],
    )
    provider_latency_seconds = Histogram(
        "xiaozhi_provider_latency_seconds",
        "Upstream provider end-to-end latency",
        ["component", "provider"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 15, 30, 60, 120),
    )
    provider_ttfb_seconds = Histogram(
        "xiaozhi_provider_ttfb_seconds",
        "Upstream provider time to first token/byte",
        ["component", "provider"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 15, 30),
    )
    queue_depth = Gauge(
        "xiaozhi_queue_depth",
        "In-process queue depth",
        ["queue"],
    )
    circuit_state = Gauge(
        "xiaozhi_circuit_state",
        "Circuit breaker state (0=closed, 1=half_open, 2=open)",
        ["name"],
    )
    degraded_total = Counter(
        "xiaozhi_degraded_total",
        "Degradation / fallback events",
        ["stage", "kind"],
    )
    overload_shed_total = Counter(
        "xiaozhi_overload_shed_total",
        "Turns shed due to overload backpressure",
        ["reason"],
    )

    # 同步配置上限
    conn_cfg = server.get("connection") or {}
    try:
        ws_max_connections.set(float(conn_cfg.get("max_connections", 500)))
    except Exception:
        pass

    _ENABLED = True
    return True


def is_enabled() -> bool:
    return _ENABLED


def set_ws_active(count: int) -> None:
    if _ENABLED and ws_active is not None:
        ws_active.set(count)


def set_ws_max_connections(limit: int) -> None:
    if _ENABLED and ws_max_connections is not None:
        ws_max_connections.set(limit)


def observe_ws_rejected(reason: str) -> None:
    if not _ENABLED or ws_rejected_total is None:
        return
    # 归一化 label，避免高基数
    label = "capacity" if "capacity" in reason else "device_limit" if "device" in reason else "other"
    ws_rejected_total.labels(reason=label).inc()


def observe_ws_opened() -> None:
    if _ENABLED and ws_sessions_opened_total is not None:
        ws_sessions_opened_total.inc()


def observe_ws_session_duration(seconds: float) -> None:
    if _ENABLED and ws_session_duration_seconds is not None and seconds >= 0:
        ws_session_duration_seconds.observe(seconds)


def observe_provider(
    component: str,
    provider: str,
    duration: float,
    status: str = "ok",
    ttfb: Optional[float] = None,
) -> None:
    if not _ENABLED:
        return
    provider = (provider or "unknown")[:64]
    component = (component or "unknown")[:32]
    status = status if status in ("ok", "error", "empty") else "error"
    if provider_requests_total is not None:
        provider_requests_total.labels(
            component=component, provider=provider, status=status
        ).inc()
    if provider_latency_seconds is not None and duration >= 0:
        provider_latency_seconds.labels(
            component=component, provider=provider
        ).observe(duration)
    if ttfb is not None and provider_ttfb_seconds is not None and ttfb >= 0:
        provider_ttfb_seconds.labels(
            component=component, provider=provider
        ).observe(ttfb)


def set_queue_depth(queue_name: str, depth: int) -> None:
    if _ENABLED and queue_depth is not None:
        queue_depth.labels(queue=queue_name).set(max(0, int(depth)))


_CIRCUIT_STATE_VALUES = {"closed": 0, "half_open": 1, "open": 2}


def set_circuit_state(name: str, state: str) -> None:
    if not _ENABLED or circuit_state is None:
        return
    label = (name or "unknown")[:64]
    value = _CIRCUIT_STATE_VALUES.get(state, 0)
    circuit_state.labels(name=label).set(value)


def observe_degraded(stage: str, kind: str) -> None:
    if not _ENABLED or degraded_total is None:
        return
    degraded_total.labels(
        stage=(stage or "unknown")[:32],
        kind=(kind or "unknown")[:32],
    ).inc()


def observe_overload_shed(reason: str) -> None:
    if not _ENABLED or overload_shed_total is None:
        return
    # 归一化，避免高基数
    r = (reason or "unknown").split("=")[0][:32]
    overload_shed_total.labels(reason=r).inc()


@contextmanager
def track_provider(component: str, provider: str):
    """计时上下文：成功 status=ok，异常 status=error。"""
    start = time.perf_counter()
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        observe_provider(
            component, provider, time.perf_counter() - start, status=status
        )


def render_latest() -> bytes:
    """返回 Prometheus text exposition 内容。"""
    if not _ENABLED:
        return b"# metrics disabled\n"
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return generate_latest()


def content_type() -> str:
    if not _ENABLED:
        return "text/plain; charset=utf-8"
    from prometheus_client import CONTENT_TYPE_LATEST

    return CONTENT_TYPE_LATEST


def provider_name_from_obj(obj) -> str:
    if obj is None:
        return "none"
    for attr in ("provider_type", "type", "interface_type"):
        val = getattr(obj, attr, None)
        if val is not None:
            return str(getattr(val, "name", val))
    return obj.__class__.__name__
