"""WebSocket 连接硬上限与会话登记。

负责全局并发与同设备连接数限制，供 WebSocketServer 在创建 ConnectionHandler 前准入。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional, Set

from core.utils import metrics as metrics_mod


@dataclass(frozen=True)
class ConnectionLimits:
    max_connections: int = 200
    max_connections_per_device: int = 2
    report_queue_maxsize: int = 100
    cleanup_timeout_seconds: float = 10.0

    @classmethod
    def from_config(cls, server_config: dict) -> "ConnectionLimits":
        conn_cfg = server_config.get("connection") or {}
        return cls(
            max_connections=int(conn_cfg.get("max_connections", 200)),
            max_connections_per_device=int(
                conn_cfg.get("max_connections_per_device", 2)
            ),
            report_queue_maxsize=int(conn_cfg.get("report_queue_maxsize", 100)),
            cleanup_timeout_seconds=float(
                conn_cfg.get("cleanup_timeout_seconds", 10.0)
            ),
        )


class ConnectionRejected(Exception):
    """连接因达到硬上限被拒绝。"""

    def __init__(self, reason: str, close_code: int = 1013):
        super().__init__(reason)
        self.reason = reason
        self.close_code = close_code


class ConnectionRegistry:
    """线程安全（asyncio 锁）的活跃连接登记表。"""

    def __init__(self, limits: ConnectionLimits):
        self.limits = limits
        self._lock = asyncio.Lock()
        self._sessions: Dict[str, str] = {}  # session_id -> device_id
        self._by_device: Dict[str, Set[str]] = {}  # device_id -> session_ids

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def device_count(self, device_id: Optional[str]) -> int:
        if not device_id:
            return 0
        return len(self._by_device.get(device_id, ()))

    async def try_acquire(self, session_id: str, device_id: Optional[str]) -> None:
        """尝试占用一个连接名额；失败抛 ConnectionRejected。"""
        normalized_device = device_id or "unknown"
        async with self._lock:
            if session_id in self._sessions:
                return

            if self.active_count >= self.limits.max_connections:
                metrics_mod.observe_ws_rejected(
                    f"server at capacity ({self.limits.max_connections})"
                )
                raise ConnectionRejected(
                    f"server at capacity ({self.limits.max_connections})"
                )

            device_sessions = self._by_device.get(normalized_device)
            if (
                device_sessions is not None
                and len(device_sessions) >= self.limits.max_connections_per_device
            ):
                metrics_mod.observe_ws_rejected(
                    f"device connection limit ({self.limits.max_connections_per_device})"
                )
                raise ConnectionRejected(
                    f"device connection limit ({self.limits.max_connections_per_device})"
                )

            self._sessions[session_id] = normalized_device
            if device_sessions is None:
                self._by_device[normalized_device] = {session_id}
            else:
                device_sessions.add(session_id)
            metrics_mod.observe_ws_opened()
            metrics_mod.set_ws_active(self.active_count)

    async def release(self, session_id: str) -> None:
        """释放连接名额；幂等。"""
        async with self._lock:
            device_id = self._sessions.pop(session_id, None)
            if device_id is None:
                return
            device_sessions = self._by_device.get(device_id)
            if not device_sessions:
                return
            device_sessions.discard(session_id)
            if not device_sessions:
                self._by_device.pop(device_id, None)
            metrics_mod.set_ws_active(self.active_count)
