"""Prove FSM validation is enforced on access SendCommand / peer gates."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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


@pytest.fixture()
def fresh_store(monkeypatch):
    """Isolate global device_session_store used by AccessCommandServicer."""
    from app.ws import session_fsm
    from app.ws.session_fsm import DeviceSessionStore

    store = DeviceSessionStore()
    store.ensure("dev-1", session_id="sess-1")
    monkeypatch.setattr(session_fsm, "device_session_store", store)
    # access_service imports the singleton by name — patch there too
    import app.server.access_service as svc

    monkeypatch.setattr(svc, "device_session_store", store)
    return store


def _cmd(client_id: str, command: str):
    from xiaozhi import command_pb2

    return command_pb2.CommandRequest(
        message_id="m1", client_id=client_id, command=command
    )


def test_send_command_happy_path_enforced(fresh_store):
    from app.server.access_service import AccessCommandServicer
    from xiaozhi_common.session import SessionState

    svc = AccessCommandServicer()
    ctx = MagicMock()

    r = svc.SendCommand(_cmd("dev-1", "listen_start"), ctx)
    assert r.code == 0
    assert r.result == SessionState.LISTENING.value
    assert fresh_store.get_state("dev-1") == SessionState.LISTENING.value

    r = svc.SendCommand(_cmd("dev-1", "chat_start"), ctx)
    assert r.code == 0
    assert r.result == SessionState.THINKING.value

    r = svc.SendCommand(_cmd("dev-1", "tts_start"), ctx)
    assert r.code == 0
    assert r.result == SessionState.SPEAKING.value

    r = svc.SendCommand(_cmd("dev-1", "tts_end"), ctx)
    assert r.code == 0
    assert r.result == SessionState.IDLE.value


def test_send_command_rejects_illegal_transition(fresh_store):
    """IDLE → tts_end is illegal; must return code=2 and keep IDLE."""
    from app.server.access_service import AccessCommandServicer
    from xiaozhi_common.session import SessionState

    svc = AccessCommandServicer()
    ctx = MagicMock()

    r = svc.SendCommand(_cmd("dev-1", "tts_end"), ctx)
    assert r.code == 2, f"expected illegal_transition, got code={r.code} msg={r.msg}"
    assert r.msg == "illegal_transition"
    assert r.result == SessionState.IDLE.value
    assert fresh_store.get_state("dev-1") == SessionState.IDLE.value


def test_send_command_rejects_chat_while_speaking(fresh_store):
    from app.server.access_service import AccessCommandServicer
    from xiaozhi_common.session import SessionState

    svc = AccessCommandServicer()
    ctx = MagicMock()
    assert svc.SendCommand(_cmd("dev-1", "tts_start"), ctx).code == 0
    assert fresh_store.get_state("dev-1") == SessionState.SPEAKING.value

    r = svc.SendCommand(_cmd("dev-1", "chat_start"), ctx)
    assert r.code == 2
    assert fresh_store.get_state("dev-1") == SessionState.SPEAKING.value


def test_play_only_rejects_dialogue_commands(fresh_store):
    from app.server.access_service import AccessCommandServicer
    from xiaozhi_common.session import PLAY_ONLY_SESSION_MODE, SessionState

    fresh_store.ensure("dev-1").switch_mode(PLAY_ONLY_SESSION_MODE)
    svc = AccessCommandServicer()
    ctx = MagicMock()

    for cmd in ("listen_start", "detect", "chat_start", "voice_end"):
        r = svc.SendCommand(_cmd("dev-1", cmd), ctx)
        assert r.code == 2, f"{cmd} should be rejected in play_only, got {r.code}"
        assert fresh_store.get_state("dev-1") == SessionState.IDLE.value

    # TTS still allowed
    r = svc.SendCommand(_cmd("dev-1", "tts_start"), ctx)
    assert r.code == 0
    assert r.result == SessionState.SPEAKING.value


def test_get_device_state_returns_fsm_value(fresh_store):
    from app.server.access_service import AccessCommandServicer
    from app.ws.manager import connection_manager
    from xiaozhi import command_pb2
    from xiaozhi_common.session import SessionState

    # Bind so get_state prefers FSM store
    with patch.object(connection_manager, "resolve_client_id", return_value="dev-1"):
        with patch.object(connection_manager, "get_state", wraps=None) as _:
            pass
    # Direct: store is source of truth via connection_manager.get_state after ensure
    fresh_store.apply_command("dev-1", "listen_start")
    # connection_manager.get_state reads device_session_store when present
    from app.ws import session_fsm

    assert session_fsm.device_session_store.get_state("dev-1") == SessionState.LISTENING.value

    svc = AccessCommandServicer()
    # Patch connection_manager.get_state to use our store (servicer uses connection_manager)
    with patch(
        "app.server.access_service.connection_manager.get_state",
        side_effect=lambda cid: fresh_store.get_state(cid),
    ):
        resp = svc.GetDeviceState(
            command_pb2.DeviceStateRequest(message_id="m", client_id="dev-1"),
            MagicMock(),
        )
    assert resp.code == 0
    assert resp.state == SessionState.LISTENING.value


def test_peer_gates_match_micro_service_rules():
    from xiaozhi_common.session import (
        SessionState,
        can_continue_play,
        can_start_listen,
        can_start_play,
        can_start_think,
    )

    # think start: listen/idle/detect only
    assert can_start_think(SessionState.LISTENING) is True
    assert can_start_think(SessionState.IDLE) is True
    assert can_start_think(SessionState.DETECT) is True
    assert can_start_think(SessionState.THINKING) is False
    assert can_start_think(SessionState.SPEAKING) is False
    assert can_start_think(None) is False

    # play start: think/idle/speaking
    assert can_start_play(SessionState.THINKING) is True
    assert can_start_play(SessionState.IDLE) is True
    assert can_start_play(SessionState.LISTENING) is False

    assert can_continue_play(SessionState.SPEAKING) is True
    assert can_continue_play(SessionState.THINKING) is False

    assert can_start_listen(SessionState.IDLE) is True
    assert can_start_listen(SessionState.THINKING) is False


def test_gate_start_think_uses_access_state():
    """gate_start_think must deny when GetDeviceState reports SPEAKING."""
    from xiaozhi import command_pb2
    from xiaozhi_common.session import SessionState, gate_start_think

    pool = MagicMock()
    stub = MagicMock()
    stub.GetDeviceState.return_value = command_pb2.DeviceStateResponse(
        code=0, msg="ok", state=SessionState.SPEAKING.value
    )
    with patch(
        "xiaozhi_common.session.device_gates.command_pb2_grpc.AccessCommandServiceStub",
        return_value=stub,
    ):
        assert gate_start_think(pool, "c1", message_id="m") is False

    stub.GetDeviceState.return_value = command_pb2.DeviceStateResponse(
        code=0, msg="ok", state=SessionState.LISTENING.value
    )
    with patch(
        "xiaozhi_common.session.device_gates.command_pb2_grpc.AccessCommandServiceStub",
        return_value=stub,
    ):
        assert gate_start_think(pool, "c1", message_id="m") is True


def test_gate_start_play_denies_listening():
    from xiaozhi import command_pb2
    from xiaozhi_common.session import SessionState, gate_start_play

    pool = MagicMock()
    stub = MagicMock()
    stub.GetDeviceState.return_value = command_pb2.DeviceStateResponse(
        code=0, msg="ok", state=SessionState.LISTENING.value
    )
    with patch(
        "xiaozhi_common.session.device_gates.command_pb2_grpc.AccessCommandServiceStub",
        return_value=stub,
    ):
        assert gate_start_play(pool, "c1") is False


def test_mode_hot_reload_skips_unchanged(fresh_store):
    from xiaozhi_common.session import DEFAULT_SESSION_MODE, PLAY_ONLY_SESSION_MODE

    n = fresh_store.apply_mode_to_all(DEFAULT_SESSION_MODE, only_if_changed=True)
    assert n == 0  # already common
    n = fresh_store.apply_mode_to_all(PLAY_ONLY_SESSION_MODE, only_if_changed=True)
    assert n == 1
    assert fresh_store.get_mode("dev-1") == PLAY_ONLY_SESSION_MODE
    n = fresh_store.apply_mode_to_all(PLAY_ONLY_SESSION_MODE, only_if_changed=True)
    assert n == 0
