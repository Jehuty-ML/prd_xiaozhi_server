#!/usr/bin/env python3
"""Dialogue 注册心跳验证：真实 Redis 上走一遍注册 / 选路 / 假存活清理 / 注销。"""

from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, ".")


def ok(name: str, cond: bool, detail: str = "", errors: list | None = None):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond and errors is not None:
        errors.append(name)


async def main() -> int:
    from core.utils.circuit_store import build_redis_client
    from core.utils.dialogue_registry import (
        HEARTBEAT_KEY_PREFIX,
        REGISTRY_HASH_KEY,
        DialogueServerInfo,
        DialogueServerRegistrar,
        RedisDialogueServerRegistry,
        get_registry_settings,
    )

    errors: list[str] = []
    cfg = {
        "server": {
            "port": 8000,
            "http_port": 8003,
            "websocket": "ws://127.0.0.1:8000/xiaozhi/v1/",
            "registry": {
                "enabled": True,
                "instance_id": "verify-dialogue-smoke",
                "heartbeat_interval_seconds": 30,
                "heartbeat_ttl_seconds": 60,
                "redis": {
                    "host": "127.0.0.1",
                    "port": 6379,
                    "db": 0,
                    "socket_timeout": 1.0,
                },
            },
        }
    }
    settings = get_registry_settings(cfg)

    print("=== 1. Redis 连通 ===")
    try:
        client = build_redis_client(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            db=settings.redis_db,
            socket_timeout=settings.redis_socket_timeout,
            decode_responses=False,
        )
        # build_redis_client 默认 decode_responses=False；此处用返回值
        pong = client.ping()
        ok("ping", pong is True, f"db={settings.redis_db}", errors)
    except TypeError:
        # 兼容无 decode_responses 关键字的旧签名
        client = build_redis_client(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            db=settings.redis_db,
            socket_timeout=settings.redis_socket_timeout,
        )
        pong = client.ping()
        ok("ping", pong is True, f"db={settings.redis_db}", errors)
    except Exception as e:
        ok("ping", False, str(e), errors)
        print("Redis 不可用，终止后续步骤")
        return 1

    reg = RedisDialogueServerRegistry(
        client, heartbeat_ttl_seconds=settings.heartbeat_ttl_seconds
    )
    instance_id = "verify-dialogue-smoke"
    ws = "ws://127.0.0.1:8000/xiaozhi/v1/"
    zombie_id = "verify-dialogue-zombie"

    print("=== 2. 注册 + 选路 ===")
    # 清理可能残留
    try:
        reg.unregister(instance_id)
        reg.unregister(zombie_id)
    except Exception:
        pass

    info = DialogueServerInfo(instance_id=instance_id, websocket_address=ws)
    reg.register(info)
    raw = client.hget(REGISTRY_HASH_KEY, instance_id)
    ok("hash_written", raw is not None, str(raw)[:80] if raw else "", errors)
    hb_key = HEARTBEAT_KEY_PREFIX + instance_id
    ttl = client.ttl(hb_key)
    ok("heartbeat_ttl", isinstance(ttl, int) and 0 < ttl <= 60, f"ttl={ttl}", errors)

    picked = reg.select_server()
    ok(
        "select_live",
        picked is not None and picked.instance_id == instance_id,
        f"picked={getattr(picked, 'instance_id', None)}",
        errors,
    )
    ok(
        "select_ws",
        reg.select_websocket_url() == ws,
        str(reg.select_websocket_url()),
        errors,
    )

    print("=== 3. 假存活清理 ===")
    zombie = DialogueServerInfo(
        instance_id=zombie_id,
        websocket_address="ws://9.9.9.9:8000/xiaozhi/v1/",
    )
    reg.register(zombie)
    # 人为删掉心跳，模拟宕机后 TTL 过期
    client.delete(HEARTBEAT_KEY_PREFIX + zombie_id)
    available = reg.get_available_servers()
    ids = {s.instance_id for s in available}
    ok("zombie_excluded", zombie_id not in ids, f"alive={ids}", errors)
    ok("live_kept", instance_id in ids, f"alive={ids}", errors)
    # get_available_servers 应已懒删 hash 字段
    zombie_hash = client.hget(REGISTRY_HASH_KEY, zombie_id)
    ok("zombie_hash_cleaned", zombie_hash is None, str(zombie_hash), errors)

    print("=== 4. Registrar 启停 ===")
    registrar = DialogueServerRegistrar(cfg)
    await registrar.start()
    await asyncio.sleep(0.3)
    raw2 = client.hget(REGISTRY_HASH_KEY, instance_id)
    ok("registrar_registered", raw2 is not None, errors=errors)
    ttl2 = client.ttl(HEARTBEAT_KEY_PREFIX + instance_id)
    ok("registrar_heartbeat", isinstance(ttl2, int) and ttl2 > 0, f"ttl={ttl2}", errors)
    await registrar.stop()
    await asyncio.sleep(0.2)
    gone = client.hget(REGISTRY_HASH_KEY, instance_id)
    hb_gone = client.get(HEARTBEAT_KEY_PREFIX + instance_id)
    ok("unregister_hash", gone is None, str(gone), errors)
    ok("unregister_heartbeat", hb_gone is None, str(hb_gone), errors)

    print("=== 5. 智控台模式配置合并（registry）===")
    try:
        from config.config_loader import get_config_from_api_async

        # 不真正打 API：只测 merge 片段逻辑 —— 直接构造与 loader 相同的合并
        default_server = {
            "registry": {
                "enabled": False,
                "heartbeat_ttl_seconds": 60,
                "redis": {"db": 0},
            }
        }
        custom_server = {
            "registry": {
                "enabled": True,
                "instance_id": "from-local",
                "redis": {"host": "127.0.0.1"},
            }
        }
        from config.config_loader import merge_configs

        merged = {}
        merged = merge_configs(merged, default_server["registry"])
        merged = merge_configs(merged, custom_server["registry"])
        ok("local_override_enabled", merged.get("enabled") is True, str(merged), errors)
        ok(
            "local_override_instance",
            merged.get("instance_id") == "from-local",
            str(merged.get("instance_id")),
            errors,
        )
        ok("ttl_kept_from_default", merged.get("heartbeat_ttl_seconds") == 60, errors=errors)
    except Exception as e:
        ok("merge_check", False, str(e), errors)

    print()
    if errors:
        print(f"结果: FAIL（{len(errors)} 项）: {', '.join(errors)}")
        return 1
    print("结果: PASS — 注册 / 选路 / 假存活清理 / 注销 均符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
