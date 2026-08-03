"""熔断状态存储：本地 / Redis 多实例共享。

Redis 不可用时默认回退本地，避免共享熔断拖垮整站。
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple

TAG = __name__


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

        return _Null()


class CircuitStore(ABC):
    """跨连接共享的熔断计数与开路时间（wall clock）。"""

    @abstractmethod
    def allow(self, name: str, open_seconds: float) -> bool:
        ...

    @abstractmethod
    def record_success(self, name: str) -> None:
        ...

    @abstractmethod
    def record_failure(
        self, name: str, failure_threshold: int, open_seconds: float
    ) -> Tuple[bool, int]:
        """返回 (just_opened, failures)。"""
        ...

    @abstractmethod
    def state(self, name: str, open_seconds: float) -> str:
        ...


class LocalCircuitStore(CircuitStore):
    """进程内存储（默认）。"""

    def __init__(self):
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def allow(self, name: str, open_seconds: float) -> bool:
        with self._lock:
            entry = self._data.get(name)
            if not entry or entry.get("opened_at") is None:
                return True
            if time.time() - float(entry["opened_at"]) >= open_seconds:
                return True
            return False

    def record_success(self, name: str) -> None:
        with self._lock:
            self._data.pop(name, None)

    def record_failure(
        self, name: str, failure_threshold: int, open_seconds: float
    ) -> Tuple[bool, int]:
        with self._lock:
            entry = self._data.setdefault(name, {"failures": 0, "opened_at": None})
            entry["failures"] = int(entry.get("failures") or 0) + 1
            opened_at = entry.get("opened_at")
            half_open = False
            if opened_at is not None:
                half_open = time.time() - float(opened_at) >= open_seconds
            just_opened = False
            if half_open or entry["failures"] >= failure_threshold:
                if opened_at is None or half_open:
                    just_opened = True
                entry["opened_at"] = time.time()
            return just_opened, int(entry["failures"])

    def state(self, name: str, open_seconds: float) -> str:
        with self._lock:
            entry = self._data.get(name)
            if not entry or entry.get("opened_at") is None:
                return "closed"
            if time.time() - float(entry["opened_at"]) >= open_seconds:
                return "half_open"
            return "open"


class RedisCircuitStore(CircuitStore):
    """Redis HASH：failures / opened_at（unix 秒）。"""

    def __init__(
        self,
        client: Any,
        *,
        key_prefix: str = "xiaozhi:circuit:",
        fallback: Optional[CircuitStore] = None,
    ):
        self._client = client
        self._prefix = key_prefix or "xiaozhi:circuit:"
        self._fallback = fallback or LocalCircuitStore()
        self._warned = False

    def _key(self, name: str) -> str:
        return f"{self._prefix}{name}"

    def _use_fallback(self, op: str, err: BaseException):
        if not self._warned:
            _log().warning(
                f"Redis 熔断不可用，回退本地 ({op}): {err}"
            )
            self._warned = True
        return self._fallback

    def allow(self, name: str, open_seconds: float) -> bool:
        try:
            data = self._client.hgetall(self._key(name))
            opened_at = _hash_get(data, "opened_at")
            if not opened_at:
                return True
            if time.time() - float(opened_at) >= open_seconds:
                return True
            return False
        except Exception as e:
            return self._use_fallback("allow", e).allow(name, open_seconds)

    def record_success(self, name: str) -> None:
        try:
            self._client.delete(self._key(name))
        except Exception as e:
            self._use_fallback("record_success", e).record_success(name)

    def record_failure(
        self, name: str, failure_threshold: int, open_seconds: float
    ) -> Tuple[bool, int]:
        try:
            key = self._key(name)
            pipe = self._client.pipeline()
            pipe.hincrby(key, "failures", 1)
            pipe.hget(key, "opened_at")
            failures, opened_raw = pipe.execute()
            failures = int(failures or 0)
            opened_at = None
            if opened_raw not in (None, b"", ""):
                try:
                    opened_at = float(
                        opened_raw.decode()
                        if isinstance(opened_raw, (bytes, bytearray))
                        else opened_raw
                    )
                except (TypeError, ValueError):
                    opened_at = None
            half_open = (
                opened_at is not None and time.time() - opened_at >= open_seconds
            )
            just_opened = False
            if half_open or failures >= failure_threshold:
                if opened_at is None or half_open:
                    just_opened = True
                self._client.hset(key, "opened_at", str(time.time()))
                # 开路期间保留 key，TTL 略长于冷却，避免泄漏
                ttl = max(int(open_seconds * 3), 60)
                self._client.expire(key, ttl)
            return just_opened, failures
        except Exception as e:
            return self._use_fallback("record_failure", e).record_failure(
                name, failure_threshold, open_seconds
            )

    def state(self, name: str, open_seconds: float) -> str:
        try:
            data = self._client.hgetall(self._key(name))
            opened_at = _hash_get(data, "opened_at")
            if not opened_at:
                return "closed"
            if time.time() - float(opened_at) >= open_seconds:
                return "half_open"
            return "open"
        except Exception as e:
            return self._use_fallback("state", e).state(name, open_seconds)


def _hash_get(data: Any, field: str) -> Optional[str]:
    if not data:
        return None
    raw = data.get(field)
    if raw is None and isinstance(field, str):
        raw = data.get(field.encode())
    if raw in (None, b"", ""):
        return None
    if isinstance(raw, (bytes, bytearray)):
        return raw.decode()
    return str(raw)


_store_lock = threading.Lock()
_local_store = LocalCircuitStore()
_redis_store: Optional[RedisCircuitStore] = None
_redis_fingerprint: Optional[str] = None


def get_local_store() -> LocalCircuitStore:
    return _local_store


def reset_circuit_stores_for_tests() -> None:
    global _redis_store, _redis_fingerprint
    with _store_lock:
        _local_store._data.clear()
        _redis_store = None
        _redis_fingerprint = None


def build_redis_client(
    *,
    url: str = "",
    host: str = "127.0.0.1",
    port: int = 6379,
    password: str = "",
    db: int = 1,
    socket_timeout: float = 1.0,
) -> Any:
    import redis

    if url:
        return redis.Redis.from_url(
            url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            decode_responses=False,
        )
    return redis.Redis(
        host=host or "127.0.0.1",
        port=int(port or 6379),
        password=password or None,
        db=int(db),
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_timeout,
        decode_responses=False,
    )


def get_circuit_store(
    *,
    redis_enabled: bool = False,
    url: str = "",
    host: str = "127.0.0.1",
    port: int = 6379,
    password: str = "",
    db: int = 1,
    key_prefix: str = "xiaozhi:circuit:",
    fallback_local: bool = True,
    socket_timeout: float = 1.0,
) -> CircuitStore:
    """按配置返回本地或 Redis 共享存储。"""
    global _redis_store, _redis_fingerprint

    if not redis_enabled:
        return _local_store

    fp = f"{url}|{host}|{port}|{db}|{key_prefix}|{bool(password)}"
    with _store_lock:
        if _redis_store is not None and _redis_fingerprint == fp:
            return _redis_store
        try:
            client = build_redis_client(
                url=url,
                host=host,
                port=port,
                password=password,
                db=db,
                socket_timeout=socket_timeout,
            )
            # 探测一次，失败则整段回退
            client.ping()
            fallback = _local_store if fallback_local else None
            # fallback_local=False 时仍给一个 local，避免 None；但 allow 失败应 fail-open
            store = RedisCircuitStore(
                client,
                key_prefix=key_prefix,
                fallback=fallback or LocalCircuitStore(),
            )
            _redis_store = store
            _redis_fingerprint = fp
            _log().info(
                f"熔断状态已启用 Redis 共享: "
                f"{host}:{port}/{db} prefix={key_prefix}"
            )
            return store
        except Exception as e:
            _log().warning(f"无法连接 Redis 熔断存储，使用本地: {e}")
            _redis_store = None
            _redis_fingerprint = None
            return _local_store
