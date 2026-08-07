#!/usr/bin/env python3
"""Dialogue Redis 注册 / 心跳单测（内存假客户端，不依赖真实 Redis）。"""

from __future__ import annotations

import json
import time
import unittest
from typing import Any, Dict, List, Optional

from core.utils.dialogue_registry import (
    HEARTBEAT_KEY_PREFIX,
    REGISTRY_HASH_KEY,
    DialogueServerInfo,
    RedisDialogueServerRegistry,
    get_registry_settings,
    resolve_instance_id,
)


class _FakePipe:
    def __init__(self, store: "_FakeRedis"):
        self._store = store
        self._ops: List[tuple] = []

    def hset(self, key, field, value):
        self._ops.append(("hset", key, field, value))
        return self

    def set(self, key, value, ex=None):
        self._ops.append(("set", key, value, ex))
        return self

    def hdel(self, key, field):
        self._ops.append(("hdel", key, field))
        return self

    def delete(self, key):
        self._ops.append(("delete", key))
        return self

    def execute(self):
        for op in self._ops:
            kind = op[0]
            if kind == "hset":
                _, key, field, value = op
                self._store._hash.setdefault(key, {})[field] = value
            elif kind == "set":
                _, key, value, ex = op
                self._store._kv[key] = value
                self._store._ttl[key] = time.time() + ex if ex else None
            elif kind == "hdel":
                _, key, field = op
                self._store._hash.get(key, {}).pop(field, None)
            elif kind == "delete":
                _, key = op
                self._store._kv.pop(key, None)
                self._store._ttl.pop(key, None)
        self._ops.clear()
        return []


class _FakeRedis:
    def __init__(self):
        self._hash: Dict[str, Dict[str, Any]] = {}
        self._kv: Dict[str, Any] = {}
        self._ttl: Dict[str, Optional[float]] = {}

    def pipeline(self, transaction=True):
        return _FakePipe(self)

    def hgetall(self, key):
        return dict(self._hash.get(key, {}))

    def mget(self, keys):
        now = time.time()
        out = []
        for k in keys:
            exp = self._ttl.get(k)
            if exp is not None and exp < now:
                self._kv.pop(k, None)
                out.append(None)
            else:
                out.append(self._kv.get(k))
        return out

    def hdel(self, key, field):
        self._hash.get(key, {}).pop(field, None)

    def expire_heartbeat(self, instance_id: str):
        key = HEARTBEAT_KEY_PREFIX + instance_id
        self._kv.pop(key, None)
        self._ttl.pop(key, None)


class TestDialogueServerInfo(unittest.TestCase):
    def test_roundtrip_camel_case(self):
        info = DialogueServerInfo(
            instance_id="node-1",
            websocket_address="ws://10.0.0.1:8000/xiaozhi/v1/",
            last_heartbeat=123,
        )
        raw = info.to_redis_json()
        data = json.loads(raw)
        self.assertEqual(data["instanceId"], "node-1")
        self.assertEqual(data["websocketAddress"], "ws://10.0.0.1:8000/xiaozhi/v1/")
        parsed = DialogueServerInfo.from_redis_json(raw)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.instance_id, "node-1")
        self.assertEqual(parsed.websocket_address, "ws://10.0.0.1:8000/xiaozhi/v1/")


class TestRegistrySettings(unittest.TestCase):
    def test_defaults(self):
        s = get_registry_settings({})
        self.assertFalse(s.enabled)
        self.assertEqual(s.redis_db, 0)
        self.assertEqual(s.heartbeat_interval_seconds, 30.0)
        self.assertEqual(s.heartbeat_ttl_seconds, 60)

    def test_parse(self):
        s = get_registry_settings(
            {
                "server": {
                    "registry": {
                        "enabled": True,
                        "instance_id": "d1",
                        "heartbeat_interval_seconds": 15,
                        "redis": {"host": "10.1.1.1", "db": 0},
                    }
                }
            }
        )
        self.assertTrue(s.enabled)
        self.assertEqual(s.instance_id, "d1")
        self.assertEqual(s.heartbeat_interval_seconds, 15.0)
        self.assertEqual(s.redis_host, "10.1.1.1")


class TestRedisDialogueServerRegistry(unittest.TestCase):
    def test_register_heartbeat_select_and_expire(self):
        fake = _FakeRedis()
        reg = RedisDialogueServerRegistry(fake, heartbeat_ttl_seconds=60)
        info = DialogueServerInfo(
            instance_id="a1",
            websocket_address="ws://1.1.1.1:8000/xiaozhi/v1/",
        )
        reg.register(info)
        self.assertIn("a1", fake._hash[REGISTRY_HASH_KEY])
        self.assertEqual(fake._kv[HEARTBEAT_KEY_PREFIX + "a1"], "1")

        picked = reg.select_server()
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertEqual(picked.instance_id, "a1")
        self.assertEqual(reg.select_websocket_url(), "ws://1.1.1.1:8000/xiaozhi/v1/")

        fake.expire_heartbeat("a1")
        self.assertEqual(reg.get_available_servers(), [])
        # 僵尸 hash field 被清理
        self.assertNotIn("a1", fake._hash.get(REGISTRY_HASH_KEY, {}))

    def test_unregister(self):
        fake = _FakeRedis()
        reg = RedisDialogueServerRegistry(fake)
        info = DialogueServerInfo(
            instance_id="b1",
            websocket_address="ws://2.2.2.2:8000/xiaozhi/v1/",
        )
        reg.register(info)
        reg.unregister("b1")
        self.assertEqual(reg.get_available_servers(), [])

    def test_resolve_instance_id_from_config(self):
        iid = resolve_instance_id(
            {"server": {"registry": {"instance_id": "fixed-id"}}}
        )
        self.assertEqual(iid, "fixed-id")


if __name__ == "__main__":
    unittest.main()
