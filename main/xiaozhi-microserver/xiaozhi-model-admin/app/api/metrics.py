"""Minimal Prometheus metrics for model-admin control plane."""

from __future__ import annotations

from typing import Optional

try:
    from prometheus_client import Counter, Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST

    _REGISTRY = CollectorRegistry()
    CONFIG_RELOADS = Counter(
        "xiaozhi_admin_config_reloads_total",
        "Config reload attempts",
        ["result"],
        registry=_REGISTRY,
    )
    OTA_REQUESTS = Counter(
        "xiaozhi_admin_ota_requests_total",
        "OTA requests",
        ["method", "result"],
        registry=_REGISTRY,
    )
    ACCESS_ACTIVE = Gauge(
        "xiaozhi_access_active_connections",
        "Active WS connections reported by access",
        registry=_REGISTRY,
    )
    ACCESS_MAX = Gauge(
        "xiaozhi_access_max_connections",
        "Max WS connections reported by access",
        registry=_REGISTRY,
    )
    METRICS_AVAILABLE = True
except Exception:  # noqa: BLE001
    METRICS_AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    _REGISTRY = None
    CONFIG_RELOADS = None
    OTA_REQUESTS = None
    ACCESS_ACTIVE = None
    ACCESS_MAX = None


def observe_reload(ok: bool) -> None:
    if CONFIG_RELOADS is not None:
        CONFIG_RELOADS.labels(result="ok" if ok else "error").inc()


def observe_ota(method: str, ok: bool) -> None:
    if OTA_REQUESTS is not None:
        OTA_REQUESTS.labels(method=method, result="ok" if ok else "error").inc()


def set_access_gauges(active: Optional[int], max_conn: Optional[int]) -> None:
    if active is not None and ACCESS_ACTIVE is not None:
        ACCESS_ACTIVE.set(active)
    if max_conn is not None and ACCESS_MAX is not None:
        ACCESS_MAX.set(max_conn)


def render_latest() -> bytes:
    if not METRICS_AVAILABLE or _REGISTRY is None:
        return b"# prometheus_client not installed\n"
    return generate_latest(_REGISTRY)
