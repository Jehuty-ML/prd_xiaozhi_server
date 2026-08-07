#!/usr/bin/env python3
"""resolve_auth_key：启动解析与热更新沿用内存密钥。"""

from __future__ import annotations

import unittest

from config.config_loader import is_valid_auth_key, resolve_auth_key


class ResolveAuthKeyTests(unittest.TestCase):
    def test_prefers_server_auth_key(self):
        cfg = {
            "server": {"auth_key": "from-yaml-key-32chars!!!!!!!!!!"},
            "manager-api": {"secret": "from-secret"},
        }
        self.assertEqual(
            resolve_auth_key(cfg, allow_generate=False),
            "from-yaml-key-32chars!!!!!!!!!!",
        )

    def test_falls_back_to_manager_api_secret(self):
        cfg = {
            "server": {"auth_key": ""},
            "manager-api": {"secret": "manager-secret-value"},
        }
        self.assertEqual(
            resolve_auth_key(cfg, allow_generate=False),
            "manager-secret-value",
        )

    def test_placeholder_auth_key_is_invalid(self):
        self.assertFalse(is_valid_auth_key("请在此处填写你的密钥"))
        cfg = {
            "server": {"auth_key": "请在此处填写你的密钥"},
            "manager-api": {"secret": "manager-secret-value"},
        }
        self.assertEqual(
            resolve_auth_key(cfg, allow_generate=False),
            "manager-secret-value",
        )

    def test_hot_reload_preserves_previous_key(self):
        """YAML 未写 auth_key 时热更新应沿用内存密钥，不得落空。"""
        cfg = {"server": {}, "manager-api": {"secret": ""}}
        prev = "runtime-uuid-or-secret-abcdefgh"
        self.assertEqual(
            resolve_auth_key(cfg, previous_key=prev, allow_generate=False),
            prev,
        )

    def test_hot_reload_without_fallback_returns_empty(self):
        cfg = {"server": {"auth_key": ""}, "manager-api": {}}
        self.assertEqual(
            resolve_auth_key(cfg, previous_key="", allow_generate=False),
            "",
        )

    def test_startup_can_generate(self):
        cfg = {"server": {}, "manager-api": {}}
        key = resolve_auth_key(cfg, allow_generate=True)
        self.assertTrue(is_valid_auth_key(key))
        self.assertEqual(len(key), 32)


if __name__ == "__main__":
    unittest.main()
