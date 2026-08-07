"""WebSocket connection hard limits (ported from xiaozhi-server)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, Optional, Set


@dataclass(frozen=True)
class ConnectionLimits:
    max_connections: int = 200
    max_connections_per_device: int = 2
    cleanup_timeout_seconds: float = 10.0

    @classmethod
    def from_config(cls, server_config: dict) -> "ConnectionLimits":
        conn_cfg = (server_config or {}).get("connection") or {}
        return cls(
            max_connections=int(conn_cfg.get("max_connections", 200)),
            max_connections_per_device=int(
                conn_cfg.get("max_connections_per_device", 2)
            ),
            cleanup_timeout_seconds=float(
                conn_cfg.get("cleanup_timeout_seconds", 10.0)
            ),
        )


class ConnectionRejected(Exception):
    def __init__(self, reason: str, close_code: int = 1013):
        super().__init__(reason)
        self.reason = reason
        self.close_code = close_code


class ConnectionRegistry:
    def __init__(self, limits: ConnectionLimits):
        self.limits = limits
        self._lock = asyncio.Lock()
        self._sessions: Dict[str, str] = {}
        self._by_device: Dict[str, Set[str]] = {}
        self.rejected_total = 0

    def update_limits(self, limits: ConnectionLimits) -> None:
        self.limits = limits

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def device_count(self, device_id: Optional[str]) -> int:
        if not device_id:
            return 0
        return len(self._by_device.get(device_id, ()))

    async def try_acquire(self, session_id: str, device_id: Optional[str]) -> None:
        normalized_device = device_id or "unknown"
        async with self._lock:
            if session_id in self._sessions:
                return
            if self.active_count >= self.limits.max_connections:
                self.rejected_total += 1
                raise ConnectionRejected(
                    f"server at capacity ({self.limits.max_connections})"
                )
            device_sessions = self._by_device.get(normalized_device)
            if (
                device_sessions is not None
                and len(device_sessions) >= self.limits.max_connections_per_device
            ):
                self.rejected_total += 1
                raise ConnectionRejected(
                    f"device connection limit ({self.limits.max_connections_per_device})"
                )
            self._sessions[session_id] = normalized_device
            if device_sessions is None:
                self._by_device[normalized_device] = {session_id}
            else:
                device_sessions.add(session_id)

    async def release(self, session_id: str) -> None:
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
