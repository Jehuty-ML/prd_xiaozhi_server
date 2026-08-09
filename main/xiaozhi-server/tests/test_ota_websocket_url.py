#!/usr/bin/env python3
"""OTA 静态 websocket 回退：分号多地址必须拆成单 URL 下发。"""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def _load_ota_handler():
    fake_util = types.ModuleType("core.utils.util")
    fake_util.get_local_ip = lambda: "127.0.0.1"
    fake_util.get_vision_url = lambda _cfg: "http://127.0.0.1/vision"
    prev_util = sys.modules.get("core.utils.util")
    sys.modules["core.utils.util"] = fake_util
    for name in ("core.api.ota_handler", "core.api.base_handler"):
        sys.modules.pop(name, None)
    with patch("config.logger.setup_logging", return_value=MagicMock()):
        from core.api.ota_handler import OTAHandler

    return OTAHandler, prev_util


def _cleanup(prev_util):
    for name in ("core.api.ota_handler", "core.api.base_handler"):
        sys.modules.pop(name, None)
    if prev_util is None:
        sys.modules.pop("core.utils.util", None)
    else:
        sys.modules["core.utils.util"] = prev_util


class TestOtaStaticWebsocketUrl(unittest.TestCase):
    def test_semicolon_list_returns_single_candidate(self):
        OTAHandler, prev_util = _load_ota_handler()
        try:
            handler = OTAHandler(
                {
                    "server": {
                        "port": 8000,
                        "auth_key": "k" * 32,
                        "websocket": (
                            "ws://a.example:8000/xiaozhi/v1/;"
                            "ws://b.example:8000/xiaozhi/v1/"
                        ),
                        "auth": {"enabled": False},
                    }
                }
            )
            with patch("core.api.ota_handler.random.choice", side_effect=lambda xs: xs[0]):
                url = handler._get_static_websocket_url("10.0.0.1", 8000)
            self.assertEqual(url, "ws://a.example:8000/xiaozhi/v1/")
            self.assertNotIn(";", url)
        finally:
            _cleanup(prev_util)

    def test_semicolon_list_never_returns_joined_string(self):
        OTAHandler, prev_util = _load_ota_handler()
        try:
            joined = (
                "ws://a.example:8000/xiaozhi/v1/;ws://b.example:8000/xiaozhi/v1/"
            )
            handler = OTAHandler(
                {
                    "server": {
                        "port": 8000,
                        "auth_key": "k" * 32,
                        "websocket": joined,
                        "auth": {"enabled": False},
                    }
                }
            )
            for _ in range(20):
                url = handler._get_static_websocket_url("10.0.0.1", 8000)
                self.assertNotEqual(url, joined)
                self.assertNotIn(";", url)
                self.assertIn(url, (
                    "ws://a.example:8000/xiaozhi/v1/",
                    "ws://b.example:8000/xiaozhi/v1/",
                ))
        finally:
            _cleanup(prev_util)

    def test_single_websocket_unchanged(self):
        OTAHandler, prev_util = _load_ota_handler()
        try:
            handler = OTAHandler(
                {
                    "server": {
                        "port": 8000,
                        "auth_key": "k" * 32,
                        "websocket": "ws://only.example:8000/xiaozhi/v1/",
                        "auth": {"enabled": False},
                    }
                }
            )
            url = handler._get_static_websocket_url("10.0.0.1", 8000)
            self.assertEqual(url, "ws://only.example:8000/xiaozhi/v1/")
        finally:
            _cleanup(prev_util)

    def test_placeholder_falls_back_to_local(self):
        OTAHandler, prev_util = _load_ota_handler()
        try:
            handler = OTAHandler(
                {
                    "server": {
                        "port": 8000,
                        "auth_key": "k" * 32,
                        "websocket": "ws://你的IP:8000/xiaozhi/v1/",
                        "auth": {"enabled": False},
                    }
                }
            )
            url = handler._get_static_websocket_url("10.0.0.1", 8000)
            self.assertEqual(url, "ws://10.0.0.1:8000/xiaozhi/v1/")
        finally:
            _cleanup(prev_util)


if __name__ == "__main__":
    unittest.main()
