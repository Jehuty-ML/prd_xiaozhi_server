"""WS alias must not route a disconnected primary id to another device."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

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


def test_alias_cleared_on_primary_unbind():
    from app.ws.manager import ConnectionManager
    from app.ws import session_fsm
    from app.ws.session_fsm import DeviceSessionStore

    store = DeviceSessionStore()
    session_fsm.device_session_store = store

    cm = ConnectionManager()
    ws_a = MagicMock()
    ws_b = MagicMock()

    cm.bind("macA", ws_a, session_id="sA", device_id="macA")
    # B tries to alias client-id=macA while A is live → refused
    cm.bind("macB", ws_b, session_id="sB", device_id="macB", alias_id="macA")
    assert cm.resolve_client_id("macA") == "macA"

    cm.unbind(ws_a)
    # After A leaves, stale alias must not send macA traffic to B
    assert cm.resolve_client_id("macA") is None
    assert cm.resolve_client_id("macB") == "macB"


def test_primary_bind_reclaims_stale_alias():
    from app.ws.manager import ConnectionManager
    from app.ws import session_fsm
    from app.ws.session_fsm import DeviceSessionStore

    store = DeviceSessionStore()
    session_fsm.device_session_store = store

    cm = ConnectionManager()
    ws_b = MagicMock()
    ws_a = MagicMock()

    # B online first with alias macA (A offline)
    cm.bind("macB", ws_b, session_id="sB", device_id="macB", alias_id="macA")
    assert cm.resolve_client_id("macA") == "macB"

    # A reconnects as primary — reclaim alias key
    cm.bind("macA", ws_a, session_id="sA", device_id="macA")
    assert cm.resolve_client_id("macA") == "macA"
