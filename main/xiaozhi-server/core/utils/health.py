"""进程健康检查：liveness / readiness。

- /health：进程存活（liveness），供编排重启判定
- /ready：能否接新流量（readiness），供 LB / 滚动发布摘流
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple

from core.utils.dialogue_registry import get_registry_settings
from core.utils.runtime_env import (
    resolve_auth_enabled,
    resolve_devices_allowlist_only,
    resolve_environment,
    resolve_whitelist_bypass_allowed,
)

TAG = __name__


class HealthState:
    """由 app.py 在启动时绑定运行时依赖。"""

    def __init__(self) -> None:
        self.config: Dict[str, Any] = {}
        self.ws_server: Any = None
        self.dialogue_registrar: Any = None
        self.http_started: bool = False

    def bind(
        self,
        *,
        config: Dict[str, Any],
        ws_server: Any = None,
        dialogue_registrar: Any = None,
    ) -> None:
        self.config = config or {}
        if ws_server is not None:
            self.ws_server = ws_server
        if dialogue_registrar is not None:
            self.dialogue_registrar = dialogue_registrar

    def mark_http_started(self) -> None:
        self.http_started = True


health_state = HealthState()


def _health_cfg(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    server = (config or health_state.config or {}).get("server") or {}
    raw = server.get("health") if isinstance(server.get("health"), dict) else {}
    return raw


def get_health_paths(config: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    cfg = _health_cfg(config)
    live = str(cfg.get("liveness_path") or "/health")
    ready = str(cfg.get("readiness_path") or "/ready")
    if not live.startswith("/"):
        live = "/" + live
    if not ready.startswith("/"):
        ready = "/" + ready
    return live, ready


def build_liveness_payload(
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = config or health_state.config or {}
    return {
        "status": "ok",
        "environment": resolve_environment(cfg),
    }


async def _check_registry_redis(
    config: Dict[str, Any],
) -> Dict[str, Any]:
    settings = get_registry_settings(config)
    if not settings.enabled:
        return {"ok": True, "skipped": True, "reason": "registry disabled"}

    registrar = health_state.dialogue_registrar
    # 已有连接则直接 ping，避免每次新建客户端
    if registrar is not None and getattr(registrar, "registry", None) is not None:
        client = getattr(registrar.registry, "_client", None) or getattr(
            registrar.registry, "client", None
        )
        if client is not None:
            try:
                await asyncio.to_thread(client.ping)
                return {"ok": True}
            except Exception as e:
                return {"ok": False, "detail": str(e)}

    try:
        from core.utils.circuit_store import build_redis_client

        def _ping() -> None:
            c = build_redis_client(
                url=settings.redis_url,
                host=settings.redis_host,
                port=settings.redis_port,
                password=settings.redis_password,
                db=settings.redis_db,
                socket_timeout=settings.redis_socket_timeout,
            )
            try:
                c.ping()
            finally:
                try:
                    c.close()
                except Exception:
                    pass

        await asyncio.to_thread(_ping)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


async def build_readiness_payload(
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    cfg = config or health_state.config or {}
    health_cfg = _health_cfg(cfg)
    reject_when_full = bool(health_cfg.get("reject_when_at_capacity", True))
    checks: Dict[str, Any] = {}
    ready = True

    if not health_state.http_started:
        checks["http"] = {"ok": False, "detail": "http server not started"}
        ready = False
    else:
        checks["http"] = {"ok": True}

    ws = health_state.ws_server
    if ws is None or not hasattr(ws, "connection_registry"):
        checks["websocket"] = {"ok": False, "detail": "websocket server not bound"}
        ready = False
    else:
        active = int(ws.connection_registry.active_count)
        max_c = int(ws.connection_limits.max_connections)
        at_capacity = active >= max_c
        checks["websocket"] = {
            "ok": True,
            "active_connections": active,
            "max_connections": max_c,
            "at_capacity": at_capacity,
        }
        if reject_when_full and at_capacity:
            checks["capacity"] = {
                "ok": False,
                "detail": f"at capacity ({active}/{max_c})",
            }
            ready = False
        else:
            checks["capacity"] = {"ok": True}

    redis_check = await _check_registry_redis(cfg)
    checks["registry_redis"] = redis_check
    if not redis_check.get("ok", False):
        ready = False

    # 生产环境提示：auth / 白名单策略（仅信息，不挡 ready）
    checks["auth"] = {
        "ok": True,
        "enabled": resolve_auth_enabled(cfg),
        "environment": resolve_environment(cfg),
        "whitelist_bypass": resolve_whitelist_bypass_allowed(cfg),
        "devices_allowlist_only": resolve_devices_allowlist_only(cfg),
    }

    payload = {
        "status": "ready" if ready else "not_ready",
        "environment": resolve_environment(cfg),
        "checks": checks,
    }
    return ready, payload
