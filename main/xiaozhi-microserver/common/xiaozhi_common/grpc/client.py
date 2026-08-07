from __future__ import annotations

from typing import Any, Optional

import grpc
from loguru import logger

from xiaozhi_common.nacos.resolver import ServiceResolver


class GrpcClientPool:
    def __init__(self, resolver: ServiceResolver) -> None:
        self.resolver = resolver
        self._channels: dict[str, grpc.Channel] = {}

    def channel(self, service_name: str) -> grpc.Channel:
        host, port = self.resolver.pick(service_name)
        key = f"{host}:{port}"
        if key not in self._channels:
            logger.debug(f"Open channel {service_name} -> {key}")
            self._channels[key] = grpc.insecure_channel(key)
        return self._channels[key]

    def metadata(self, client_id: str = "", message_id: str = "") -> list[tuple[str, str]]:
        md = []
        if client_id:
            md.append(("client_id", client_id))
        if message_id:
            md.append(("message_id", message_id))
        return md

    def close(self) -> None:
        for ch in self._channels.values():
            ch.close()
        self._channels.clear()
