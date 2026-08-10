"""Session state machine: legal transitions, play_only, illegal rejects."""

from __future__ import annotations

import sys
from pathlib import Path

MICRO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MICRO_ROOT / "common"))


class _MemLog:
    def __init__(self) -> None:
        self.info_msgs: list[str] = []
        self.warn_msgs: list[str] = []
        self.debug_msgs: list[str] = []

    def info(self, msg: str) -> None:
        self.info_msgs.append(msg)

    def warning(self, msg: str) -> None:
        self.warn_msgs.append(msg)

    def debug(self, msg: str) -> None:
        self.debug_msgs.append(msg)


def test_happy_path_and_illegal():
    from xiaozhi_common.session import SessionEvent, SessionState, SessionStateMachine

    log = _MemLog()
    sm = SessionStateMachine(session_id="s1", logger=log)
    assert sm.state == SessionState.IDLE
    assert sm.transition(SessionEvent.LISTEN_START)
    assert sm.state == SessionState.LISTENING
    assert sm.transition(SessionEvent.CHAT_START)
    assert sm.state == SessionState.THINKING
    assert sm.transition(SessionEvent.TTS_START)
    assert sm.state == SessionState.SPEAKING
    assert sm.transition(SessionEvent.TTS_END)
    assert sm.state == SessionState.IDLE
    # Illegal: IDLE cannot TTS_END
    assert not sm.transition(SessionEvent.TTS_END)
    assert any("illegal_transition" in m for m in log.warn_msgs)


def test_play_only_matrix():
    from xiaozhi_common.session import (
        PLAY_ONLY_SESSION_MODE,
        SESSION_MACHINE_PROFILES,
        SessionEvent,
        SessionState,
        SessionStateMachine,
        is_play_only_mode,
    )

    profile = SESSION_MACHINE_PROFILES[PLAY_ONLY_SESSION_MODE]
    assert (SessionState.IDLE, SessionEvent.TTS_START) in profile.transitions
    assert (SessionState.IDLE, SessionEvent.CHAT_START) not in profile.transitions

    sm = SessionStateMachine(session_id="po", mode=PLAY_ONLY_SESSION_MODE)
    assert is_play_only_mode(sm.mode)
    assert not sm.transition(SessionEvent.LISTEN_START)
    assert not sm.transition(SessionEvent.CHAT_START)
    assert sm.transition(SessionEvent.TTS_START)
    assert sm.state == SessionState.SPEAKING
    assert sm.transition(SessionEvent.TTS_END)
    assert sm.state == SessionState.IDLE


def test_resolve_mode_and_switch():
    from xiaozhi_common.session import (
        DEFAULT_SESSION_MODE,
        PLAY_ONLY_SESSION_MODE,
        SessionEvent,
        SessionStateMachine,
        resolve_session_mode,
    )

    assert resolve_session_mode({}) == DEFAULT_SESSION_MODE
    assert (
        resolve_session_mode({"session_state": {"mode": "play_only"}})
        == PLAY_ONLY_SESSION_MODE
    )
    assert resolve_session_mode({"session_state": {"mode": "game"}}) == DEFAULT_SESSION_MODE

    log = _MemLog()
    sm = SessionStateMachine(session_id="m1", logger=log)
    assert not sm.switch_mode("game")
    assert sm.switch_mode(PLAY_ONLY_SESSION_MODE)
    assert sm.mode == PLAY_ONLY_SESSION_MODE
    assert not sm.transition(SessionEvent.DETECT)


def test_peer_gates():
    from xiaozhi_common.session import (
        SessionState,
        can_start_listen,
        can_start_play,
        can_start_think,
        parse_session_state,
    )

    assert can_start_think(SessionState.LISTENING)
    assert can_start_think(SessionState.IDLE)
    assert not can_start_think(SessionState.SPEAKING)
    assert can_start_play(SessionState.THINKING)
    assert can_start_listen(SessionState.IDLE)
    assert parse_session_state("listening") == SessionState.LISTENING
    assert parse_session_state("THINKING") == SessionState.THINKING
    assert parse_session_state("aborted") == SessionState.IDLE


def test_access_device_session_store():
    """Import access store with path pin."""
    access_root = MICRO_ROOT / "xiaozhi-access"
    sys.path.insert(0, str(access_root))
    # Clear app modules if speaker/agent polluted them
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]
    from app.ws.session_fsm import DeviceSessionStore
    from xiaozhi_common.session import SessionEvent, SessionState

    store = DeviceSessionStore()
    store.ensure("c1")
    ok, state = store.apply_command("c1", "listen_start")
    assert ok and state == SessionState.LISTENING.value
    ok, state = store.apply_command("c1", "chat_start")
    assert ok and state == SessionState.THINKING.value
    ok, state = store.apply_command("c1", "tts_start")
    assert ok and state == SessionState.SPEAKING.value
    # Illegal while speaking: chat_start not in matrix from SPEAKING
    ok, state = store.apply_command("c1", "chat_start")
    assert not ok
    assert state == SessionState.SPEAKING.value
    assert store.transition("c1", SessionEvent.TTS_END)
    assert store.get_state("c1") == SessionState.IDLE.value
