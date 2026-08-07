"""Mutable gateway config applied at startup and via ConfigApplyService."""

from __future__ import annotations

import copy
import threading
from typing import Any, Optional

from loguru import logger

from xiaozhi_common.auth import AuthManager
from xiaozhi_common.runtime_env import (
    normalize_allowed_devices,
    resolve_auth_enabled,
    resolve_devices_allowlist_only,
    resolve_environment,
    resolve_whitelist_bypass_allowed,
)
from app.ws.connection_registry import ConnectionLimits, ConnectionRegistry


DEFAULT_GATEWAY_CONFIG: dict[str, Any] = {
    "server": {
        "name": "xiaozhi-access",
        "phase": 2,
        "environment": "development",
        "auth_key": "",
        "auth": {
            "enabled": False,
            "expire_seconds": 60 * 60 * 24 * 30,
            "allowed_devices": [],
            "allow_whitelist_bypass": True,
            "devices_allowlist_only": False,
        },
        "connection": {
            "max_connections": 200,
            "max_connections_per_device": 2,
        },
        "enable_websocket_ping": True,
    },
    "manager-api": {
        "url": "",
        "secret": "",
    },
    "manager_api": {
        "url": "",
        "secret": "",
        "enabled": False,
    },
    "xiaozhi": {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 24000,
            "channels": 1,
            "frame_duration": 60,
        },
    },
    "read_config_from_api": False,
}


class GatewayRuntime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.config: dict[str, Any] = copy.deepcopy(DEFAULT_GATEWAY_CONFIG)
        self.auth_enable = False
        self.allowed_devices: set = set()
        self.whitelist_bypass = True
        self.devices_allowlist_only = False
        self.auth = AuthManager(secret_key="")
        self.registry = ConnectionRegistry(ConnectionLimits())
        self._apply_auth_from_config(log=False)

    def apply_config(self, config: dict[str, Any], reason: str = "apply") -> None:
        with self._lock:
            merged = copy.deepcopy(DEFAULT_GATEWAY_CONFIG)
            _deep_merge(merged, config or {})
            self.config = merged
            self._apply_auth_from_config(log=True)
            server = self.config.get("server") or {}
            self.registry.update_limits(ConnectionLimits.from_config(server))
            logger.info(
                f"Gateway config applied reason={reason} "
                f"env={resolve_environment(self.config)} "
                f"auth={self.auth_enable} "
                f"max_conn={self.registry.limits.max_connections}"
            )

    def _apply_auth_from_config(self, *, log: bool) -> None:
        server = self.config.get("server") or {}
        auth_config = server.get("auth") or {}
        self.auth_enable = resolve_auth_enabled(self.config)
        self.allowed_devices = normalize_allowed_devices(
            auth_config.get("allowed_devices")
        )
        self.whitelist_bypass = resolve_whitelist_bypass_allowed(self.config)
        self.devices_allowlist_only = resolve_devices_allowlist_only(self.config)
        secret_key = server.get("auth_key") or ""
        expire_seconds = auth_config.get("expire_seconds")
        self.auth = AuthManager(secret_key=secret_key, expire_seconds=expire_seconds)
        if log:
            logger.info(
                f"Access auth: {'enabled' if self.auth_enable else 'disabled'} "
                f"(env={resolve_environment(self.config)}, "
                f"whitelist_bypass={self.whitelist_bypass}, "
                f"allowlist_only={self.devices_allowlist_only})"
            )

    def welcome_message(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            msg = copy.deepcopy(self.config.get("xiaozhi") or {})
            msg["session_id"] = session_id
            if "type" not in msg:
                msg["type"] = "hello"
            return msg

    def manager_secret(self) -> str:
        with self._lock:
            api = self.config.get("manager-api") or self.config.get("manager_api") or {}
            return str(api.get("secret") or "")

    def read_config_from_api(self) -> bool:
        with self._lock:
            return bool(self.config.get("read_config_from_api"))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.config)


def _deep_merge(base: dict, overlay: dict) -> dict:
    for key, value in (overlay or {}).items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


gateway_runtime = GatewayRuntime()
