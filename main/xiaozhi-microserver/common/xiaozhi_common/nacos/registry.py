from __future__ import annotations

import time
from threading import Thread
from typing import Any, Optional

from loguru import logger

from xiaozhi_common.config import BaseServerConfig


class NacosRegistry:
    """Register + heartbeat with optional Nacos; no-op when client is None."""

    def __init__(
        self,
        config: BaseServerConfig,
        nacos_client: Any,
        ip: str,
        port: int,
        metadata: Optional[dict] = None,
    ) -> None:
        self.config = config
        self.client = nacos_client
        self.ip = ip
        self.port = port
        self.metadata = metadata or {"grpc": "true"}
        self._stop = False

    def register(self) -> None:
        if not self.client:
            logger.info(
                f"Skip Nacos register for {self.config.service_name} ({self.ip}:{self.port})"
            )
            return
        logger.info(
            f"Register {self.config.service_name} -> {self.ip}:{self.port} "
            f"group={self.config.group_name}"
        )
        self.client.add_naming_instance(
            self.config.service_name,
            self.ip,
            self.port,
            cluster_name="DEFAULT",
            weight=1.0,
            metadata=self.metadata,
            enable=True,
            healthy=True,
            ephemeral=True,
            group_name=self.config.group_name,
        )

    def _heartbeat_loop(self) -> None:
        fail_count = 0
        max_interval = 60
        while not self._stop:
            if not self.client:
                time.sleep(self.config.heart_interval)
                continue
            try:
                if fail_count > 0:
                    self.register()
                    fail_count = 0
                self.client.send_heartbeat(
                    self.config.service_name,
                    self.ip,
                    self.port,
                    cluster_name="DEFAULT",
                    weight=1.0,
                    metadata=self.metadata,
                    group_name=self.config.group_name,
                )
                time.sleep(self.config.heart_interval)
            except Exception as exc:  # noqa: BLE001
                fail_count += 1
                sleep_time = min(
                    self.config.heart_interval * (2 ** (fail_count - 1)), max_interval
                )
                logger.error(
                    f"Heartbeat failed (#{fail_count}) for {self.config.service_name}: {exc}; "
                    f"retry in {sleep_time}s"
                )
                time.sleep(sleep_time)

    def start_heartbeat(self) -> Thread:
        thread = Thread(target=self._heartbeat_loop, daemon=True, name="nacos-heartbeat")
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop = True
