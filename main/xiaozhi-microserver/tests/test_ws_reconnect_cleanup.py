"""Reconnect must not wipe the new session or leak registry slots."""

from __future__ import annotations

import asyncio
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


def test_replaced_socket_is_not_active():
    from app.ws.manager import ConnectionManager
    from app.ws import session_fsm
    from app.ws.session_fsm import DeviceSessionStore

    session_fsm.device_session_store = DeviceSessionStore()
    cm = ConnectionManager()
    old = MagicMock(name="old")
    new = MagicMock(name="new")

    cm.bind("mac1", old, session_id="s-old", device_id="mac1")
    assert cm.is_active_socket(old) is True

    prev = cm.bind("mac1", new, session_id="s-new", device_id="mac1")
    assert prev is old
    assert cm.is_active_socket(old) is False
    assert cm.is_active_socket(new) is True
    # Stale unbind must not clear the new primary.
    cm.unbind(old)
    assert cm.get_client_id(new) == "mac1"
    assert cm.active_count == 1


def test_registry_release_on_replace_allows_rapid_reconnect():
    from app.ws.connection_registry import ConnectionLimits, ConnectionRegistry

    async def _run() -> None:
        reg = ConnectionRegistry(
            ConnectionLimits(max_connections=10, max_connections_per_device=2)
        )
        await reg.try_acquire("s1", "mac1")
        await reg.try_acquire("s2", "mac1")
        # Without releasing the replaced slot, a third acquire would fail.
        await reg.release("s1")
        await reg.try_acquire("s3", "mac1")
        assert reg.device_count("mac1") == 2
        assert "s1" not in reg._sessions
        assert "s2" in reg._sessions and "s3" in reg._sessions

    asyncio.run(_run())
