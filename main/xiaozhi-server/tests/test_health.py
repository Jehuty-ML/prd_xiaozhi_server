#!/usr/bin/env python3
"""健康检查单测（不依赖真实 Redis / 运行中的 WS）。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from core.utils.health import (
    build_liveness_payload,
    build_readiness_payload,
    get_health_paths,
    health_state,
)


class _FakeRegistry:
    def __init__(self, active: int = 0):
        self._active = active

    @property
    def active_count(self) -> int:
        return self._active


class TestHealthHelpers(unittest.TestCase):
    def setUp(self) -> None:
        health_state.config = {}
        health_state.ws_server = None
        health_state.dialogue_registrar = None
        health_state.http_started = False

    def test_paths_default_and_custom(self):
        live, ready = get_health_paths({})
        self.assertEqual(live, "/health")
        self.assertEqual(ready, "/ready")
        live, ready = get_health_paths(
            {"server": {"health": {"liveness_path": "livez", "readiness_path": "readyz"}}}
        )
        self.assertEqual(live, "/livez")
        self.assertEqual(ready, "/readyz")

    def test_liveness_ok(self):
        payload = build_liveness_payload({"server": {"environment": "production"}})
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["environment"], "production")

    def test_readiness_not_ready_before_http(self):
        ready, payload = asyncio.run(
            build_readiness_payload({"server": {"environment": "development"}})
        )
        self.assertFalse(ready)
        self.assertEqual(payload["status"], "not_ready")
        self.assertFalse(payload["checks"]["http"]["ok"])

    def test_readiness_at_capacity(self):
        health_state.http_started = True
        health_state.ws_server = SimpleNamespace(
            connection_registry=_FakeRegistry(active=2),
            connection_limits=SimpleNamespace(max_connections=2),
        )
        cfg = {
            "server": {
                "environment": "development",
                "health": {"reject_when_at_capacity": True},
                "registry": {"enabled": False},
            }
        }
        ready, payload = asyncio.run(build_readiness_payload(cfg))
        self.assertFalse(ready)
        self.assertFalse(payload["checks"]["capacity"]["ok"])

    def test_readiness_ok_when_under_capacity(self):
        health_state.http_started = True
        health_state.ws_server = SimpleNamespace(
            connection_registry=_FakeRegistry(active=1),
            connection_limits=SimpleNamespace(max_connections=10),
        )
        cfg = {
            "server": {
                "environment": "production",
                "auth": {"enabled": True},
                "registry": {"enabled": False},
            }
        }
        ready, payload = asyncio.run(build_readiness_payload(cfg))
        self.assertTrue(ready)
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["checks"]["auth"]["enabled"])
        self.assertFalse(payload["checks"]["auth"]["whitelist_bypass"])


if __name__ == "__main__":
    unittest.main()
