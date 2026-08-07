from __future__ import annotations

import random
import threading
import time
from typing import Any, Optional

from loguru import logger

from xiaozhi_common.config import BaseServerConfig
from xiaozhi_common.constants import DEFAULT_PORTS


class ServiceResolver:
    """Resolve host:port via static peers, then Nacos, then DEFAULT_PORTS."""

    def __init__(
        self,
        config: BaseServerConfig,
        nacos_client: Any = None,
        refresh_seconds: int = 5,
    ) -> None:
        self.config = config
        self.client = nacos_client
        self.refresh_seconds = refresh_seconds
        self._cache: dict[str, list[tuple[str, int]]] = {}
        self._indices: dict[str, int] = {}
        self._lock = threading.Lock()
        self._names: set[str] = set()
        self._running = True
        Thread = __import__("threading").Thread
        self._thread = Thread(target=self._refresh_loop, daemon=True)
        self._thread.start()

    def watch(self, service_name: str) -> None:
        self._names.add(service_name)
        self._indices.setdefault(service_name, 0)
        self.refresh()

    def refresh(self) -> None:
        for name in list(self._names):
            addrs = self._resolve_once(name)
            if addrs:
                with self._lock:
                    self._cache[name] = addrs

    def _resolve_once(self, service_name: str) -> list[tuple[str, int]]:
        if service_name in self.config.peers:
            host, port_s = self.config.peers[service_name].rsplit(":", 1)
            return [(host, int(port_s))]

        if self.client:
            try:
                result = self.client.list_naming_instance(
                    service_name=service_name,
                    group_name=self.config.group_name,
                    healthy_only=True,
                )
                hosts = []
                for ins in (result or {}).get("hosts", []):
                    port = int(ins.get("metadata", {}).get("gRPC_port") or ins["port"])
                    hosts.append((ins["ip"], port))
                if hosts:
                    return hosts
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"Nacos discover {service_name} failed: {exc}")

        if service_name in DEFAULT_PORTS:
            return [("127.0.0.1", DEFAULT_PORTS[service_name])]
        return []

    def _refresh_loop(self) -> None:
        while self._running:
            try:
                self.refresh()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"resolver refresh error: {exc}")
            time.sleep(self.refresh_seconds)

    def pick(self, service_name: str) -> tuple[str, int]:
        with self._lock:
            addrs = list(self._cache.get(service_name) or [])
        if not addrs:
            addrs = self._resolve_once(service_name)
            if addrs:
                with self._lock:
                    self._cache[service_name] = addrs
        if not addrs:
            raise RuntimeError(f"No instance for {service_name}")
        with self._lock:
            idx = self._indices.get(service_name, 0)
            host, port = addrs[idx % len(addrs)]
            self._indices[service_name] = idx + 1
        return host, port

    def pick_random(self, service_name: str) -> tuple[str, int]:
        with self._lock:
            addrs = list(self._cache.get(service_name) or [])
        if not addrs:
            addrs = self._resolve_once(service_name)
        if not addrs:
            raise RuntimeError(f"No instance for {service_name}")
        return random.choice(addrs)

    def stop(self) -> None:
        self._running = False
