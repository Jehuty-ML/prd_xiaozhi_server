"""Fire-and-forget chat history publisher (pika). Failures are logged only."""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Optional

from loguru import logger

from xiaozhi_common.mq.config import RabbitMqSettings, load_rabbitmq_settings

try:
    import pika
except ImportError:  # pragma: no cover
    pika = None  # type: ignore


class ChatHistoryPublisher:
    def __init__(self, settings: RabbitMqSettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._connection = None
        self._channel = None

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "ChatHistoryPublisher":
        return cls(load_rabbitmq_settings(config))

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    def close(self) -> None:
        with self._lock:
            try:
                if self._connection and not self._connection.is_closed:
                    self._connection.close()
            except Exception:  # noqa: BLE001
                pass
            self._connection = None
            self._channel = None

    def _channel_ready(self):
        if pika is None:
            logger.warning("pika not installed; chat history publish skipped")
            return None
        if (
            self._channel is not None
            and self._connection is not None
            and not self._connection.is_closed
            and self._channel.is_open
        ):
            return self._channel
        creds = pika.PlainCredentials(self.settings.username, self.settings.password)
        params = pika.ConnectionParameters(
            host=self.settings.host,
            port=self.settings.port,
            virtual_host=self.settings.virtual_host,
            credentials=creds,
            heartbeat=30,
            blocked_connection_timeout=30,
        )
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        self._channel.queue_declare(queue=self.settings.queue, durable=True)
        return self._channel

    def publish(
        self,
        *,
        mac_address: str,
        session_id: str,
        chat_type: int,
        content: str,
        report_time: Optional[int] = None,
        audio_base64: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> bool:
        if not self.enabled:
            return False
        text = (content or "").strip()
        if not text or not mac_address or not session_id:
            return False
        payload = {
            "macAddress": mac_address,
            "sessionId": session_id,
            "chatType": int(chat_type),
            "content": text,
            "audioBase64": audio_base64,
            "reportTime": int(report_time or time.time()),
            "messageId": message_id or uuid.uuid4().hex,
        }
        body = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            try:
                channel = self._channel_ready()
                if channel is None:
                    return False
                channel.basic_publish(
                    exchange="",
                    routing_key=self.settings.queue,
                    body=body.encode("utf-8"),
                    properties=pika.BasicProperties(
                        content_type="application/json",
                        delivery_mode=2,
                        message_id=payload["messageId"],
                    ),
                )
                logger.debug(
                    f"chat history published type={chat_type} "
                    f"session={session_id} len={len(text)}"
                )
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"chat history publish failed: {type(exc).__name__}: {exc!r} "
                    f"host={self.settings.host}:{self.settings.port} "
                    f"queue={self.settings.queue}"
                )
                self.close()
                return False


_default: Optional[ChatHistoryPublisher] = None
_default_lock = threading.Lock()


def publish_chat_history(
    config: dict[str, Any] | None,
    *,
    mac_address: str,
    session_id: str,
    chat_type: int,
    content: str,
    report_time: Optional[int] = None,
) -> bool:
    """Module-level helper with a process-wide publisher singleton."""
    global _default
    with _default_lock:
        settings = load_rabbitmq_settings(config)
        if _default is None or _default.settings != settings:
            if _default is not None:
                _default.close()
            _default = ChatHistoryPublisher(settings)
        pub = _default
    return pub.publish(
        mac_address=mac_address,
        session_id=session_id,
        chat_type=chat_type,
        content=content,
        report_time=report_time,
    )
