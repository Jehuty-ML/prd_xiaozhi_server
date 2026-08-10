#!/usr/bin/env python3
"""Vision 鉴权：生产禁止 web_test_client 绕过；热更新须重建 AuthToken。"""

from __future__ import annotations

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _load_vision():
    fake_util = types.ModuleType("core.utils.util")
    fake_util.get_vision_url = lambda _cfg: "http://127.0.0.1/vision"
    fake_util.is_valid_image_file = lambda _b: True
    fake_util.get_local_ip = lambda: "127.0.0.1"
    fake_util.audio_to_data = lambda *_a, **_k: []
    fake_vllm = types.ModuleType("core.utils.vllm")
    fake_vllm.create_instance = lambda *_a, **_k: None
    fake_plugins = types.ModuleType("plugins_func.register")
    fake_plugins.Action = SimpleNamespace(RESPONSE=SimpleNamespace(name="RESPONSE"))
    saved = {
        name: sys.modules.get(name)
        for name in (
            "core.utils.util",
            "core.utils.vllm",
            "plugins_func.register",
            "core.api.vision_handler",
            "core.api.base_handler",
        )
    }
    for name, mod in (
        ("core.utils.util", fake_util),
        ("core.utils.vllm", fake_vllm),
        ("plugins_func.register", fake_plugins),
    ):
        sys.modules[name] = mod
    for name in ("core.api.vision_handler", "core.api.base_handler"):
        sys.modules.pop(name, None)
    with patch("config.logger.setup_logging", return_value=MagicMock()):
        from core.api.vision_handler import VisionHandler

    return VisionHandler, saved


def _restore(saved):
    for name, old in saved.items():
        if old is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old


class TestVisionAuth(unittest.TestCase):
    def test_web_test_client_allowed_only_in_development(self):
        VisionHandler, saved = _load_vision()
        try:
            dev = VisionHandler(
                {
                    "server": {
                        "environment": "development",
                        "auth_key": "k" * 32,
                    }
                }
            )
            req = SimpleNamespace(
                headers={"Client-Id": "web_test_client", "Device-Id": "dev1"}
            )
            ok, device = dev._verify_auth_token(req)
            self.assertTrue(ok)
            self.assertEqual(device, "dev1")

            prod = VisionHandler(
                {
                    "server": {
                        "environment": "production",
                        "auth_key": "k" * 32,
                    }
                }
            )
            ok2, device2 = prod._verify_auth_token(req)
            self.assertFalse(ok2)
            self.assertIsNone(device2)
        finally:
            _restore(saved)

    def test_apply_config_rebuilds_auth_token(self):
        VisionHandler, saved = _load_vision()
        try:
            handler = VisionHandler(
                {
                    "server": {
                        "environment": "development",
                        "auth_key": "a" * 32,
                    }
                }
            )
            old_auth = handler.auth
            handler.apply_config(
                {
                    "server": {
                        "environment": "production",
                        "auth_key": "b" * 32,
                    }
                }
            )
            self.assertIsNot(handler.auth, old_auth)
            self.assertEqual(
                handler.config["server"]["environment"], "production"
            )
            # 生产下 web_test_client 不得再绕过
            req = SimpleNamespace(
                headers={"Client-Id": "web_test_client", "Device-Id": "x"}
            )
            ok, _ = handler._verify_auth_token(req)
            self.assertFalse(ok)
        finally:
            _restore(saved)


if __name__ == "__main__":
    unittest.main()
