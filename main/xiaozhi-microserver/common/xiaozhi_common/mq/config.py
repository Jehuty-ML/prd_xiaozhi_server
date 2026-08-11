"""RabbitMQ settings from service config dict."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class RabbitMqSettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 5672
    username: str = "guest"
    password: str = "guest"
    virtual_host: str = "/"
    queue: str = "xiaozhi.chat.history"


def load_rabbitmq_settings(config: dict[str, Any] | None) -> RabbitMqSettings:
    cfg = config or {}
    block = cfg.get("rabbitmq") or {}
    if not isinstance(block, dict):
        block = {}
    enabled = bool(block.get("enabled", False))
    chat = cfg.get("chat_history") or {}
    if isinstance(chat, dict) and chat.get("report_enabled") is False:
        enabled = False
    # Unit-test / CI marker: never attempt a live broker connection.
    if os.environ.get("XIAOZHI_UNIT_TEST", "").strip() in ("1", "true", "TRUE", "yes"):
        enabled = False
    return RabbitMqSettings(
        enabled=enabled,
        host=str(block.get("host") or "127.0.0.1"),
        port=int(block.get("port") or 5672),
        username=str(block.get("username") or "guest"),
        password=str(block.get("password") or "guest"),
        virtual_host=str(block.get("virtual_host") or "/"),
        queue=str(block.get("queue") or "xiaozhi.chat.history"),
    )
