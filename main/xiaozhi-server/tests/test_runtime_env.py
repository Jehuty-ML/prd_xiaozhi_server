#!/usr/bin/env python3
"""runtime_env 认证/白名单策略单测。"""

from __future__ import annotations

import os
import unittest

from core.utils.runtime_env import (
    is_device_permitted,
    normalize_allowed_devices,
    resolve_auth_enabled,
    resolve_devices_allowlist_only,
    resolve_whitelist_bypass_allowed,
    should_bypass_token_for_device,
)


class TestWhitelistPolicy(unittest.TestCase):
    def setUp(self) -> None:
        for key in ("XIAOZHI_ENV", "APP_ENV"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key in ("XIAOZHI_ENV", "APP_ENV"):
            os.environ.pop(key, None)

    def test_production_disables_bypass_by_default(self):
        cfg = {"server": {"environment": "production", "auth": {"enabled": True}}}
        self.assertTrue(resolve_auth_enabled(cfg))
        self.assertFalse(resolve_whitelist_bypass_allowed(cfg))
        self.assertFalse(
            should_bypass_token_for_device(cfg, "aa:bb", {"aa:bb"})
        )

    def test_development_allows_bypass_by_default(self):
        cfg = {
            "server": {
                "environment": "development",
                "auth": {"enabled": True, "allowed_devices": ["aa:bb"]},
            }
        }
        self.assertTrue(resolve_whitelist_bypass_allowed(cfg))
        self.assertTrue(should_bypass_token_for_device(cfg, "aa:bb", {"aa:bb"}))
        self.assertFalse(should_bypass_token_for_device(cfg, "cc:dd", {"aa:bb"}))

    def test_explicit_bypass_true_in_production(self):
        cfg = {
            "server": {
                "environment": "production",
                "auth": {"allow_whitelist_bypass": True},
            }
        }
        self.assertTrue(resolve_whitelist_bypass_allowed(cfg))
        self.assertTrue(should_bypass_token_for_device(cfg, "aa:bb", {"aa:bb"}))

    def test_auto_string_follows_environment(self):
        cfg = {
            "server": {
                "environment": "production",
                "auth": {"allow_whitelist_bypass": "auto"},
            }
        }
        self.assertFalse(resolve_whitelist_bypass_allowed(cfg))
        cfg["server"]["environment"] = "development"
        self.assertTrue(resolve_whitelist_bypass_allowed(cfg))

    def test_allowlist_only(self):
        cfg = {
            "server": {
                "environment": "production",
                "auth": {
                    "devices_allowlist_only": True,
                    "allowed_devices": ["aa:bb"],
                },
            }
        }
        self.assertTrue(resolve_devices_allowlist_only(cfg))
        self.assertTrue(is_device_permitted(cfg, "aa:bb", {"aa:bb"}))
        self.assertFalse(is_device_permitted(cfg, "cc:dd", {"aa:bb"}))

    def test_normalize_allowed_devices(self):
        self.assertEqual(normalize_allowed_devices([" a ", "", "b"]), {"a", "b"})
        self.assertEqual(normalize_allowed_devices("a,b;c"), {"a", "b", "c"})

    def test_ota_handler_apply_config_refreshes_auth(self):
        """热更新后 OTA 必须跟上 auth / 白名单，不能沿用启动缓存。"""
        import sys
        import types
        from unittest.mock import MagicMock, patch

        # ota_handler → util 依赖 opus；注入轻量假模块
        fake_util = types.ModuleType("core.utils.util")
        fake_util.get_local_ip = lambda: "127.0.0.1"
        fake_util.get_vision_url = lambda _cfg: "http://127.0.0.1/vision"
        prev_util = sys.modules.get("core.utils.util")
        sys.modules["core.utils.util"] = fake_util
        # 清掉可能半加载的 ota_handler / base_handler
        sys.modules.pop("core.api.ota_handler", None)
        sys.modules.pop("core.api.base_handler", None)
        try:
            # BaseHandler.__init__ → setup_logging() 会读本地/API 配置，单测隔离之
            with patch("config.logger.setup_logging", return_value=MagicMock()):
                from core.api.ota_handler import OTAHandler

                cfg1 = {
                    "server": {
                        "environment": "development",
                        "auth_key": "k" * 32,
                        "auth": {
                            "enabled": False,
                            "allowed_devices": [],
                            "allow_whitelist_bypass": "auto",
                            "devices_allowlist_only": False,
                        },
                    }
                }
                handler = OTAHandler(cfg1)
                self.assertFalse(handler.auth_enable)

                cfg2 = {
                    "server": {
                        "environment": "production",
                        "auth_key": "k" * 32,
                        "auth": {
                            "enabled": True,
                            "allowed_devices": ["aa:bb"],
                            "allow_whitelist_bypass": "auto",
                            "devices_allowlist_only": True,
                        },
                    }
                }
                handler.apply_config(cfg2)
                self.assertTrue(handler.auth_enable)
                self.assertEqual(handler.allowed_devices, {"aa:bb"})
                self.assertTrue(handler.devices_allowlist_only)
                self.assertFalse(handler.whitelist_bypass)
                self.assertFalse(
                    is_device_permitted(cfg2, "cc:dd", handler.allowed_devices)
                )
        finally:
            sys.modules.pop("core.api.ota_handler", None)
            sys.modules.pop("core.api.base_handler", None)
            if prev_util is None:
                sys.modules.pop("core.utils.util", None)
            else:
                sys.modules["core.utils.util"] = prev_util


if __name__ == "__main__":
    unittest.main()
