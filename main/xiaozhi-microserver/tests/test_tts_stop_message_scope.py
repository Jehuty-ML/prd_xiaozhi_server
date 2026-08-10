"""Stale TTS stop must not clobber the next turn's FSM state."""

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


def test_stale_tts_stop_ignored_after_new_turn(monkeypatch):
    from app.server.access_service import AccessAudioServicer
    from app.ws import manager as mgr_mod
    from app.ws import session_fsm
    from app.ws.session_fsm import DeviceSessionStore
    from xiaozhi import audio_pb2
    from xiaozhi_common.session import SessionEvent, SessionState

    store = DeviceSessionStore()
    store.ensure("dev-1")
    monkeypatch.setattr(session_fsm, "device_session_store", store)

    # Minimal connection meta for message_id scoping
    class _CM:
        def __init__(self):
            self._meta = {"dev-1": {}}

        def get_meta(self, client_id):
            return self._meta.setdefault(client_id, {})

        def set_meta(self, client_id, key, value):
            self._meta.setdefault(client_id, {})[key] = value

        def send_text_threadsafe(self, client_id, text):
            return True

        def send_bytes_threadsafe(self, client_id, data):
            return True

        def transition(self, client_id, event, detail=""):
            return store.transition(client_id, event, detail=detail)

    cm = _CM()
    monkeypatch.setattr(mgr_mod, "connection_manager", cm)
    import app.server.access_service as svc

    monkeypatch.setattr(svc, "connection_manager", cm)
    monkeypatch.setattr(svc, "device_session_store", store)

    servicer = AccessAudioServicer()
    ctx = MagicMock()

    # Turn A starts
    store.transition("dev-1", SessionEvent.CHAT_START)
    r = servicer.SendTtsAudio(
        audio_pb2.TtsAudioRequest(
            client_id="dev-1",
            message_id="turn-a",
            state="start",
            audio=b"",
        ),
        ctx,
    )
    assert r.code == 0
    assert store.get_state("dev-1") == SessionState.SPEAKING.value
    assert cm.get_meta("dev-1").get("tts_message_id") == "turn-a"

    # Turn A ends early in FSM via speak_end path alternative: move to THINKING for B
    store.transition("dev-1", SessionEvent.ABORT)
    store.transition("dev-1", SessionEvent.CHAT_START)
    assert store.get_state("dev-1") == SessionState.THINKING.value

    # Turn B starts speaking
    r = servicer.SendTtsAudio(
        audio_pb2.TtsAudioRequest(
            client_id="dev-1",
            message_id="turn-b",
            state="start",
            audio=b"",
        ),
        ctx,
    )
    assert r.code == 0
    assert store.get_state("dev-1") == SessionState.SPEAKING.value
    assert cm.get_meta("dev-1").get("tts_message_id") == "turn-b"

    # Late stop from turn A must not IDLE the new turn
    r = servicer.SendTtsAudio(
        audio_pb2.TtsAudioRequest(
            client_id="dev-1",
            message_id="turn-a",
            state="stop",
            audio=b"",
        ),
        ctx,
    )
    assert r.code == 0
    assert store.get_state("dev-1") == SessionState.SPEAKING.value

    # Matching stop ends the turn
    r = servicer.SendTtsAudio(
        audio_pb2.TtsAudioRequest(
            client_id="dev-1",
            message_id="turn-b",
            state="stop",
            audio=b"",
        ),
        ctx,
    )
    assert r.code == 0
    assert store.get_state("dev-1") == SessionState.IDLE.value
