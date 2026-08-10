"""Dialogue 实例 Redis 注册 / 心跳（对齐 Java RedisDialogueServerRegistry）。

Redis key：
  - Hash  `xiaozhi:dialogue:servers`          field=instanceId, value=JSON
  - String `xiaozhi:dialogue:heartbeat:{id}`  value="1", TTL=heartbeat_ttl

manager-api OTA 只挑心跳仍在的实例；无存活实例时回退静态 `server.websocket`。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.utils.circuit_store import build_redis_client

TAG = __name__

REGISTRY_HASH_KEY = "xiaozhi:dialogue:servers"
HEARTBEAT_KEY_PREFIX = "xiaozhi:dialogue:heartbeat:"

_lock = threading.Lock()
_active_registry: Optional["RedisDialogueServerRegistry"] = None
_active_registrar: Optional["DialogueServerRegistrar"] = None


def _get_local_ip() -> str:
    """避免顶层 import util（会拉 opus 等重依赖，拖垮单测）。"""
    try:
        from core.utils.util import get_local_ip

        return get_local_ip()
    except Exception:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"


def _log():
    try:
        from loguru import logger as _logger

        return _logger.bind(tag=TAG)
    except Exception:

        class _Null:
            def info(self, *a, **k):
                pass

            def warning(self, *a, **k):
                pass

            def error(self, *a, **k):
                pass

            def debug(self, *a, **k):
                pass

        return _Null()


@dataclass
class RegistrySettings:
    enabled: bool = False
    instance_id: str = ""
    heartbeat_interval_seconds: float = 30.0
    heartbeat_ttl_seconds: int = 60
    redis_url: str = ""
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0
    redis_socket_timeout: float = 1.0


@dataclass
class DialogueServerInfo:
    instance_id: str
    websocket_address: str
    udp_address: str = ""
    ota_address: str = ""
    mcp_address: str = ""
    server_address: str = ""
    last_heartbeat: int = 0

    def to_redis_json(self) -> str:
        """字段名用 camelCase，与 Java DialogueServerInfo / manager-api 对齐。"""
        return json.dumps(
            {
                "instanceId": self.instance_id,
                "websocketAddress": self.websocket_address,
                "udpAddress": self.udp_address,
                "otaAddress": self.ota_address,
                "mcpAddress": self.mcp_address,
                "serverAddress": self.server_address,
                "lastHeartbeat": int(self.last_heartbeat),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_redis_json(cls, raw: Any) -> Optional["DialogueServerInfo"]:
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            data = json.loads(raw)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        instance_id = str(data.get("instanceId") or "").strip()
        ws = str(data.get("websocketAddress") or "").strip()
        if not instance_id or not ws:
            return None
        try:
            last_hb = int(data.get("lastHeartbeat") or 0)
        except (TypeError, ValueError):
            last_hb = 0
        return cls(
            instance_id=instance_id,
            websocket_address=ws,
            udp_address=str(data.get("udpAddress") or ""),
            ota_address=str(data.get("otaAddress") or ""),
            mcp_address=str(data.get("mcpAddress") or ""),
            server_address=str(data.get("serverAddress") or ""),
            last_heartbeat=last_hb,
        )


def get_registry_settings(config: Optional[Dict[str, Any]] = None) -> RegistrySettings:
    config = config or {}
    server = config.get("server") if isinstance(config.get("server"), dict) else {}
    raw = server.get("registry") if isinstance(server.get("registry"), dict) else {}
    redis_cfg = raw.get("redis") if isinstance(raw.get("redis"), dict) else {}

    def _float(v: Any, default: float) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _int(v: Any, default: int) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    return RegistrySettings(
        enabled=bool(raw.get("enabled", False)),
        instance_id=str(raw.get("instance_id") or "").strip(),
        heartbeat_interval_seconds=max(
            5.0, _float(raw.get("heartbeat_interval_seconds"), 30.0)
        ),
        heartbeat_ttl_seconds=max(10, _int(raw.get("heartbeat_ttl_seconds"), 60)),
        redis_url=str(redis_cfg.get("url") or "").strip(),
        redis_host=str(redis_cfg.get("host") or "127.0.0.1"),
        redis_port=_int(redis_cfg.get("port"), 6379),
        redis_password=str(redis_cfg.get("password") or ""),
        # 必须与 manager-api 同库（默认 0）；勿与熔断用的 db=1 混用
        redis_db=_int(redis_cfg.get("db"), 0),
        redis_socket_timeout=max(0.2, _float(redis_cfg.get("socket_timeout"), 1.0)),
    )


def resolve_instance_id(config: Optional[Dict[str, Any]] = None) -> str:
    settings = get_registry_settings(config)
    if settings.instance_id:
        return settings.instance_id
    env_id = (os.environ.get("XIAOZHI_INSTANCE_ID") or "").strip()
    if env_id:
        return env_id
    host = (socket.gethostname() or "xiaozhi").strip() or "xiaozhi"
    return f"{host}-{uuid.uuid4().hex[:8]}"


def resolve_websocket_address(config: Optional[Dict[str, Any]] = None) -> str:
    """本实例对外 WebSocket 地址（供 OTA / Redis 注册心跳）。

    优先级：
    1. server.registry.advertise_websocket（或 websocket_address / websocket）
    2. 环境变量 XIAOZHI_WEBSOCKET_URL
    3. 单值 server.websocket
    4. 本机 IP + port 推导

    注意：server.websocket 含分号时是静态集群选路列表，不能把首项挂到
    每个存活实例上——否则实例 B 心跳仍广告 A，A 宕机后 OTA 仍下发死地址。
    """
    config = config or {}
    server = config.get("server") if isinstance(config.get("server"), dict) else {}
    registry = (
        server.get("registry") if isinstance(server.get("registry"), dict) else {}
    )

    for key in ("advertise_websocket", "websocket_address", "websocket"):
        adv = str(registry.get(key) or "").strip()
        if adv and "你" not in adv and ";" not in adv:
            return adv

    env_ws = (os.environ.get("XIAOZHI_WEBSOCKET_URL") or "").strip()
    if env_ws and "你" not in env_ws and ";" not in env_ws:
        return env_ws

    ws = str(server.get("websocket") or "").strip()
    if ws and "你" not in ws:
        if ";" in ws:
            port = int(server.get("port", 8000) or 8000)
            return f"ws://{_get_local_ip()}:{port}/xiaozhi/v1/"
        return ws
    port = int(server.get("port", 8000) or 8000)
    return f"ws://{_get_local_ip()}:{port}/xiaozhi/v1/"


def resolve_ota_address(config: Optional[Dict[str, Any]] = None) -> str:
    config = config or {}
    server = config.get("server") if isinstance(config.get("server"), dict) else {}
    # 智控台模式下设备走 manager-api OTA；此处仅作登记元数据
    http_port = int(server.get("http_port", 8003) or 8003)
    return f"http://{_get_local_ip()}:{http_port}/xiaozhi/ota/"


def resolve_udp_address(config: Optional[Dict[str, Any]] = None) -> str:
    config = config or {}
    server = config.get("server") if isinstance(config.get("server"), dict) else {}
    udp = server.get("udp_gateway")
    if udp and str(udp).strip() and str(udp).lower() != "null":
        return str(udp).strip()
    return ""


def resolve_mcp_address(config: Optional[Dict[str, Any]] = None) -> str:
    config = config or {}
    mcp = str(config.get("mcp_endpoint") or "").strip()
    if mcp and "你" not in mcp:
        return mcp
    return ""


def build_server_info(config: Optional[Dict[str, Any]] = None) -> DialogueServerInfo:
    return DialogueServerInfo(
        instance_id=resolve_instance_id(config),
        websocket_address=resolve_websocket_address(config),
        udp_address=resolve_udp_address(config),
        ota_address=resolve_ota_address(config),
        mcp_address=resolve_mcp_address(config),
        server_address="",
        last_heartbeat=int(time.time() * 1000),
    )


class RedisDialogueServerRegistry:
    """与 Java RedisDialogueServerRegistry 同协议的注册中心客户端。"""

    def __init__(
        self,
        client: Any,
        *,
        heartbeat_ttl_seconds: int = 60,
    ):
        self._client = client
        self._heartbeat_ttl = max(10, int(heartbeat_ttl_seconds))

    def register(self, info: DialogueServerInfo) -> None:
        info.last_heartbeat = int(time.time() * 1000)
        pipe = self._client.pipeline(True)
        pipe.hset(REGISTRY_HASH_KEY, info.instance_id, info.to_redis_json())
        pipe.set(
            HEARTBEAT_KEY_PREFIX + info.instance_id,
            "1",
            ex=self._heartbeat_ttl,
        )
        pipe.execute()

    def unregister(self, instance_id: str) -> None:
        if not instance_id:
            return
        pipe = self._client.pipeline(True)
        pipe.hdel(REGISTRY_HASH_KEY, instance_id)
        pipe.delete(HEARTBEAT_KEY_PREFIX + instance_id)
        pipe.execute()

    def heartbeat(self, info: DialogueServerInfo) -> None:
        # 与 register 同写：刷新元数据 + TTL
        self.register(info)

    def get_available_servers(self) -> List[DialogueServerInfo]:
        result: List[DialogueServerInfo] = []
        try:
            entries = self._client.hgetall(REGISTRY_HASH_KEY) or {}
        except Exception as e:
            _log().error(f"读取 Dialogue 注册表失败: {e}")
            return result
        if not entries:
            return result

        # hgetall 可能返回 bytes key/value
        items: List[tuple] = []
        for k, v in entries.items():
            key = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
            items.append((key, v))

        heartbeat_keys = [HEARTBEAT_KEY_PREFIX + key for key, _ in items]
        try:
            heartbeats = self._client.mget(heartbeat_keys)
        except Exception as e:
            _log().error(f"读取 Dialogue 心跳失败: {e}")
            return result

        for i, (instance_id, raw) in enumerate(items):
            hb = heartbeats[i] if heartbeats and i < len(heartbeats) else None
            if hb is None:
                try:
                    self._client.hdel(REGISTRY_HASH_KEY, instance_id)
                    _log().info(f"清理过期的 Dialogue 服务器: {instance_id}")
                except Exception:
                    pass
                continue
            info = DialogueServerInfo.from_redis_json(raw)
            if info is not None:
                result.append(info)
        return result

    def select_server(self) -> Optional[DialogueServerInfo]:
        servers = self.get_available_servers()
        if not servers:
            return None
        import random

        return random.choice(servers)

    def select_websocket_url(self) -> Optional[str]:
        picked = self.select_server()
        if picked is None:
            return None
        return picked.websocket_address or None


class DialogueServerRegistrar:
    """启动注册、定时心跳、退出注销。"""

    def __init__(self, config: Dict[str, Any]):
        self._config = config
        self._settings = get_registry_settings(config)
        # 进程生命周期内固定 instance_id，避免未配置时每次心跳换新 UUID
        self._instance_id = resolve_instance_id(config)
        self._info = self._build_info()
        self._registry: Optional[RedisDialogueServerRegistry] = None
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def registry(self) -> Optional[RedisDialogueServerRegistry]:
        return self._registry

    def _build_info(self) -> DialogueServerInfo:
        info = build_server_info(self._config)
        info.instance_id = self._instance_id
        return info

    def refresh_config(self, config: Dict[str, Any]) -> None:
        """热更新后刷新配置快照（保留 instance_id）。

        websocket / registry Redis 等变更会体现在后续心跳；Redis 连接参数
        变化时丢弃旧 client，下次心跳重建。
        """
        old = self._settings
        self._config = config or {}
        self._settings = get_registry_settings(self._config)
        self._info = self._build_info()
        if (
            old.redis_url != self._settings.redis_url
            or old.redis_host != self._settings.redis_host
            or old.redis_port != self._settings.redis_port
            or old.redis_password != self._settings.redis_password
            or old.redis_db != self._settings.redis_db
            or old.redis_socket_timeout != self._settings.redis_socket_timeout
        ):
            self._registry = None

    async def push_heartbeat(self) -> None:
        """立即上报一次心跳（热更新 websocket 后避免 OTA 长时间拿旧地址）。"""
        if not self._settings.enabled:
            return
        await self._beat_once()

    def _connect(self) -> RedisDialogueServerRegistry:
        client = build_redis_client(
            url=self._settings.redis_url,
            host=self._settings.redis_host,
            port=self._settings.redis_port,
            password=self._settings.redis_password,
            db=self._settings.redis_db,
            socket_timeout=self._settings.redis_socket_timeout,
        )
        client.ping()
        return RedisDialogueServerRegistry(
            client,
            heartbeat_ttl_seconds=self._settings.heartbeat_ttl_seconds,
        )

    async def start(self) -> None:
        if not self._settings.enabled:
            _log().info("Dialogue 注册心跳未启用 (server.registry.enabled=false)")
            return
        try:
            self._registry = await asyncio.to_thread(self._connect)
            self._info = self._build_info()
            await asyncio.to_thread(self._registry.register, self._info)
            _set_active(self._registry, self)
            _log().info(
                f"Dialogue 已注册到 Redis, instanceId={self._instance_id}, "
                f"ws={self._info.websocket_address}"
            )
        except Exception as e:
            _log().warning(
                f"Dialogue 初次注册失败，将在后续心跳重试, "
                f"instanceId={self._instance_id}: {e}"
            )
            self._registry = None

        self._stop.clear()
        self._task = asyncio.create_task(
            self._heartbeat_loop(), name="dialogue-heartbeat"
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._registry is not None:
            try:
                await asyncio.to_thread(
                    self._registry.unregister, self._instance_id
                )
                _log().info(
                    f"Dialogue 已从注册中心注销, instanceId={self._instance_id}"
                )
            except Exception as e:
                _log().warning(f"Dialogue 注销失败: {e}")
        _clear_active(self)

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            interval = self._settings.heartbeat_interval_seconds
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break
            except asyncio.TimeoutError:
                pass
            await self._beat_once()

    async def _beat_once(self) -> None:
        self._info = self._build_info()
        try:
            if self._registry is None:
                self._registry = await asyncio.to_thread(self._connect)
                _set_active(self._registry, self)
            await asyncio.to_thread(self._registry.heartbeat, self._info)
            _log().debug(f"Dialogue 心跳成功, instanceId={self._instance_id}")
        except Exception as e:
            _log().warning(f"Dialogue 心跳失败, instanceId={self._instance_id}: {e}")
            self._registry = None


def _set_active(
    registry: Optional[RedisDialogueServerRegistry],
    registrar: Optional[DialogueServerRegistrar],
) -> None:
    global _active_registry, _active_registrar
    with _lock:
        _active_registry = registry
        _active_registrar = registrar


def _clear_active(registrar: DialogueServerRegistrar) -> None:
    global _active_registry, _active_registrar
    with _lock:
        if _active_registrar is registrar:
            _active_registry = None
            _active_registrar = None


def get_active_registry() -> Optional[RedisDialogueServerRegistry]:
    with _lock:
        return _active_registry


def select_registered_websocket_url() -> Optional[str]:
    """OTA 侧：有存活注册实例则随机选一个 WS 地址。"""
    registry = get_active_registry()
    if registry is None:
        return None
    try:
        return registry.select_websocket_url()
    except Exception as e:
        _log().warning(f"从注册中心选路失败: {e}")
        return None
