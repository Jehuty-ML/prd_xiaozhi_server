from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from xiaozhi_common.config import BaseServerConfig


def create_nacos_client(config: BaseServerConfig) -> Any:
    if config.disable_nacos:
        logger.info("Nacos disabled via --disable_nacos")
        return None
    try:
        from nacos import NacosClient
    except ImportError:
        logger.warning("nacos-sdk-python not installed; Nacos disabled")
        return None

    try:
        client = NacosClient(
            server_addresses=config.nacos_addr,
            namespace=config.env_id,
            username=config.user_name,
            password=config.password,
        )
        logger.info(f"Nacos client ready: {config.nacos_addr} ns={config.env_id}")
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Nacos init failed: {exc}; continuing without Nacos")
        return None
