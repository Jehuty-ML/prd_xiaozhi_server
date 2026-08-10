"""Unit tests for chat-history RabbitMQ publisher (no broker)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from xiaozhi_common.mq.config import load_rabbitmq_settings
from xiaozhi_common.mq.publisher import ChatHistoryPublisher


def test_load_settings_disabled_by_default():
    s = load_rabbitmq_settings({})
    assert s.enabled is False
    assert s.queue == "xiaozhi.chat.history"


def test_report_enabled_false_disables_mq():
    s = load_rabbitmq_settings(
        {"rabbitmq": {"enabled": True}, "chat_history": {"report_enabled": False}}
    )
    assert s.enabled is False


def test_publish_noop_when_disabled():
    pub = ChatHistoryPublisher.from_config({"rabbitmq": {"enabled": False}})
    assert pub.publish(
        mac_address="aa", session_id="s", chat_type=1, content="hi"
    ) is False


def test_publish_payload_shape():
    pub = ChatHistoryPublisher.from_config(
        {
            "rabbitmq": {
                "enabled": True,
                "host": "127.0.0.1",
                "queue": "xiaozhi.chat.history",
            }
        }
    )
    channel = MagicMock()
    channel.is_open = True
    with patch.object(pub, "_channel_ready", return_value=channel):
        with patch("xiaozhi_common.mq.publisher.pika") as pika_mod:
            pika_mod.BasicProperties = MagicMock(return_value=MagicMock())
            ok = pub.publish(
                mac_address="aa:bb",
                session_id="sess",
                chat_type=2,
                content="你好",
                report_time=1745657732,
                message_id="mid1",
            )
    assert ok is True
    channel.basic_publish.assert_called_once()
    kwargs = channel.basic_publish.call_args.kwargs
    assert kwargs["routing_key"] == "xiaozhi.chat.history"
    body = json.loads(kwargs["body"].decode("utf-8"))
    assert body["macAddress"] == "aa:bb"
    assert body["sessionId"] == "sess"
    assert body["chatType"] == 2
    assert body["content"] == "你好"
    assert body["reportTime"] == 1745657732
    assert body["messageId"] == "mid1"
    assert body["audioBase64"] is None
