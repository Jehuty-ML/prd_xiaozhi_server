"""broadcast_speak must fail closed when manager secret is unset."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MICRO_ROOT = Path(__file__).resolve().parents[1]
ACCESS_ROOT = MICRO_ROOT / "xiaozhi-access"


def _pin_access() -> None:
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]
    paths = [
        str(ACCESS_ROOT),
        str(MICRO_ROOT / "common"),
        str(MICRO_ROOT / "common" / "generated"),
    ]
    for s in paths:
        if s in sys.path:
            sys.path.remove(s)
    for s in reversed(paths):
        sys.path.insert(0, s)


_pin_access()


@pytest.fixture(autouse=True)
def _access_path():
    _pin_access()
    yield


def test_broadcast_speak_rejects_when_secret_unset():
    from app.ws import protocol

    ws = MagicMock()
    ws.send_text = AsyncMock()
    pool = MagicMock()

    with patch.object(protocol.gateway_runtime, "manager_secret", return_value=""):
        with patch.object(protocol, "_broadcast_speak", new_callable=AsyncMock) as bcast:
            asyncio.run(
                protocol._handle_server(
                    pool,
                    ws,
                    {
                        "type": "server",
                        "action": "broadcast_speak",
                        "content": {"text": "全员播报", "secret": ""},
                    },
                )
            )
            bcast.assert_not_called()

    sent = json.loads(ws.send_text.await_args.args[0])
    assert sent["status"] == "error"
    assert "密钥" in sent["message"]


def test_broadcast_speak_rejects_wrong_secret():
    from app.ws import protocol

    ws = MagicMock()
    ws.send_text = AsyncMock()
    pool = MagicMock()

    with patch.object(protocol.gateway_runtime, "manager_secret", return_value="real"):
        with patch.object(protocol, "_broadcast_speak", new_callable=AsyncMock) as bcast:
            asyncio.run(
                protocol._handle_server(
                    pool,
                    ws,
                    {
                        "type": "server",
                        "action": "broadcast_speak",
                        "content": {"text": "全员播报", "secret": "wrong"},
                    },
                )
            )
            bcast.assert_not_called()

    sent = json.loads(ws.send_text.await_args.args[0])
    assert sent["status"] == "error"
