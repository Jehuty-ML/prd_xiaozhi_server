"""会话主状态机：合法转移矩阵 + 非法拒绝（对齐单体 session_state）。

同一连接同一时刻只有一个主状态。非法转移默认拒绝并打 warn；RESET 可强制回 IDLE。
ABORT 为事件而非状态（瞬时取消副作用 → IDLE）。

mode：common（默认可对话）/ play_only（仅播报）。
分布式用法参考 micro_service：access 持有状态机，对端 GetDeviceState 门禁。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Set, Tuple, Union


class SessionState(str, Enum):
    IDLE = "IDLE"
    DETECT = "DETECT"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"


class SessionEvent(str, Enum):
    DETECT = "detect"
    LISTEN_START = "listen_start"
    VOICE_END = "voice_end"
    CHAT_START = "chat_start"
    TTS_START = "tts_start"
    TTS_END = "tts_end"
    ABORT = "abort"
    IDLE_TIMEOUT = "idle_timeout"
    DETECT_TIMEOUT = "detect_timeout"
    RESET = "reset"


DEFAULT_SESSION_MODE = "common"
PLAY_ONLY_SESSION_MODE = "play_only"
PLAY_ONLY_DENY_TEXT_DEFAULT = "当前不能对话"
SUPPORTED_SESSION_MODES = (DEFAULT_SESSION_MODE, PLAY_ONLY_SESSION_MODE)

# (from_state, event) -> to_state；未出现在表中的组合视为非法（RESET 除外）
_COMMON_TRANSITIONS: Dict[Tuple[SessionState, SessionEvent], SessionState] = {
    (SessionState.IDLE, SessionEvent.DETECT): SessionState.DETECT,
    (SessionState.IDLE, SessionEvent.LISTEN_START): SessionState.LISTENING,
    (SessionState.IDLE, SessionEvent.CHAT_START): SessionState.THINKING,
    (SessionState.IDLE, SessionEvent.VOICE_END): SessionState.THINKING,
    (SessionState.IDLE, SessionEvent.IDLE_TIMEOUT): SessionState.THINKING,
    (SessionState.IDLE, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.IDLE, SessionEvent.ABORT): SessionState.IDLE,
    (SessionState.DETECT, SessionEvent.DETECT): SessionState.DETECT,
    (SessionState.DETECT, SessionEvent.LISTEN_START): SessionState.LISTENING,
    (SessionState.DETECT, SessionEvent.CHAT_START): SessionState.THINKING,
    (SessionState.DETECT, SessionEvent.VOICE_END): SessionState.THINKING,
    (SessionState.DETECT, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.DETECT, SessionEvent.DETECT_TIMEOUT): SessionState.IDLE,
    (SessionState.DETECT, SessionEvent.ABORT): SessionState.IDLE,
    (SessionState.LISTENING, SessionEvent.DETECT): SessionState.DETECT,
    (SessionState.LISTENING, SessionEvent.LISTEN_START): SessionState.LISTENING,
    (SessionState.LISTENING, SessionEvent.VOICE_END): SessionState.THINKING,
    (SessionState.LISTENING, SessionEvent.CHAT_START): SessionState.THINKING,
    (SessionState.LISTENING, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.LISTENING, SessionEvent.TTS_END): SessionState.LISTENING,
    (SessionState.LISTENING, SessionEvent.ABORT): SessionState.IDLE,
    (SessionState.THINKING, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.THINKING, SessionEvent.ABORT): SessionState.IDLE,
    (SessionState.THINKING, SessionEvent.CHAT_START): SessionState.THINKING,
    (SessionState.THINKING, SessionEvent.VOICE_END): SessionState.THINKING,
    (SessionState.THINKING, SessionEvent.TTS_END): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.SPEAKING, SessionEvent.TTS_END): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.ABORT): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.LISTEN_START): SessionState.LISTENING,
    (SessionState.SPEAKING, SessionEvent.DETECT): SessionState.DETECT,
}

_COMMON_EVENT_ALLOWED_FROM: Dict[SessionEvent, Set[SessionState]] = {
    SessionEvent.IDLE_TIMEOUT: {SessionState.IDLE},
    SessionEvent.DETECT_TIMEOUT: {SessionState.DETECT},
}

_PLAY_ONLY_TRANSITIONS: Dict[Tuple[SessionState, SessionEvent], SessionState] = {
    (SessionState.IDLE, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.IDLE, SessionEvent.ABORT): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.SPEAKING, SessionEvent.TTS_END): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.ABORT): SessionState.IDLE,
}

_PLAY_ONLY_EVENT_ALLOWED_FROM: Dict[SessionEvent, Set[SessionState]] = {
    SessionEvent.IDLE_TIMEOUT: set(),
    SessionEvent.DETECT_TIMEOUT: set(),
}


@dataclass(frozen=True)
class SessionMachineProfile:
    mode: str
    transitions: Dict[Tuple[SessionState, SessionEvent], SessionState]
    event_allowed_from: Dict[SessionEvent, Set[SessionState]] = field(
        default_factory=dict
    )


SESSION_MACHINE_PROFILES: Dict[str, SessionMachineProfile] = {
    DEFAULT_SESSION_MODE: SessionMachineProfile(
        mode=DEFAULT_SESSION_MODE,
        transitions=_COMMON_TRANSITIONS,
        event_allowed_from=_COMMON_EVENT_ALLOWED_FROM,
    ),
    PLAY_ONLY_SESSION_MODE: SessionMachineProfile(
        mode=PLAY_ONLY_SESSION_MODE,
        transitions=_PLAY_ONLY_TRANSITIONS,
        event_allowed_from=_PLAY_ONLY_EVENT_ALLOWED_FROM,
    ),
}

LEGAL_TRANSITIONS = _COMMON_TRANSITIONS
EVENT_ALLOWED_FROM = _COMMON_EVENT_ALLOWED_FROM

# SendCommand / 协议命令 → SessionEvent
COMMAND_TO_EVENT: Dict[str, SessionEvent] = {
    "listen_start": SessionEvent.LISTEN_START,
    "listen_stop": SessionEvent.ABORT,
    "idle": SessionEvent.RESET,
    "detect": SessionEvent.DETECT,
    "speaking": SessionEvent.TTS_START,
    "speak_start": SessionEvent.TTS_START,
    "tts_start": SessionEvent.TTS_START,
    "speak_stop": SessionEvent.TTS_END,
    "tts_end": SessionEvent.TTS_END,
    "chat_start": SessionEvent.CHAT_START,
    "think": SessionEvent.CHAT_START,
    "voice_end": SessionEvent.VOICE_END,
    "abort": SessionEvent.ABORT,
    "reset": SessionEvent.RESET,
    "detect_timeout": SessionEvent.DETECT_TIMEOUT,
    "idle_timeout": SessionEvent.IDLE_TIMEOUT,
}


def get_machine_profile(mode: Optional[str] = None) -> SessionMachineProfile:
    key = (mode or DEFAULT_SESSION_MODE).strip().lower()
    return SESSION_MACHINE_PROFILES.get(
        key, SESSION_MACHINE_PROFILES[DEFAULT_SESSION_MODE]
    )


def resolve_session_mode(config: Optional[Dict[str, Any]] = None) -> str:
    cfg = config or {}
    mode = ""
    block = cfg.get("session_state")
    if isinstance(block, dict):
        mode = str(block.get("mode") or "").strip().lower()
    if not mode:
        flat = cfg.get("session_state.mode")
        if flat is not None:
            mode = str(flat).strip().lower()
    if not mode:
        mode = DEFAULT_SESSION_MODE
    if mode not in SESSION_MACHINE_PROFILES:
        return DEFAULT_SESSION_MODE
    return mode


def is_play_only_mode(conn_or_mode: Any) -> bool:
    if isinstance(conn_or_mode, str):
        return conn_or_mode.strip().lower() == PLAY_ONLY_SESSION_MODE
    sm = getattr(conn_or_mode, "session_sm", None)
    if sm is not None:
        return getattr(sm, "mode", "") == PLAY_ONLY_SESSION_MODE
    return (
        resolve_session_mode(getattr(conn_or_mode, "config", None))
        == PLAY_ONLY_SESSION_MODE
    )


def should_block_user_dialogue_tts(holder: object) -> bool:
    return (
        is_play_only_mode(holder)
        or getattr(holder, "_broadcast_speak_active", False)
        or getattr(holder, "_broadcast_soft_barge_in", False)
    )


def get_play_only_deny_text(config: Optional[Dict[str, Any]] = None) -> str:
    cfg = config or {}
    block = cfg.get("session_state") if isinstance(cfg.get("session_state"), dict) else {}
    text = (block or {}).get("play_only_deny_text")
    if text:
        return str(text)
    flat = cfg.get("session_state.play_only_deny_text")
    if flat:
        return str(flat)
    return PLAY_ONLY_DENY_TEXT_DEFAULT


def _coerce_event(value: Union[SessionEvent, str]) -> SessionEvent:
    if isinstance(value, SessionEvent):
        return value
    raw = str(value).strip().lower()
    if raw in COMMAND_TO_EVENT:
        return COMMAND_TO_EVENT[raw]
    return SessionEvent(raw)


def parse_session_state(value: Optional[str]) -> Optional[SessionState]:
    if not value:
        return None
    raw = str(value).strip()
    # Accept legacy lowercase access states
    aliases = {
        "idle": SessionState.IDLE,
        "connected": SessionState.IDLE,
        "listening": SessionState.LISTENING,
        "listen": SessionState.LISTENING,
        "thinking": SessionState.THINKING,
        "think": SessionState.THINKING,
        "speaking": SessionState.SPEAKING,
        "play": SessionState.SPEAKING,
        "detect": SessionState.DETECT,
        "aborted": SessionState.IDLE,
        "unknown": None,
    }
    low = raw.lower()
    if low in aliases:
        return aliases[low]
    try:
        return SessionState(raw.upper())
    except ValueError:
        return None


class SessionStateMachine:
    """连接级主状态机（按 mode 选用转移表）。"""

    def __init__(
        self,
        session_id: str = "",
        initial: SessionState = SessionState.IDLE,
        logger: Optional[object] = None,
        mode: Optional[str] = None,
    ):
        self.session_id = session_id or "-"
        self._state = initial
        self._logger = logger
        resolved = (mode or DEFAULT_SESSION_MODE).strip().lower()
        if resolved not in SESSION_MACHINE_PROFILES:
            resolved = DEFAULT_SESSION_MODE
        self._mode = resolved
        self._profile = get_machine_profile(self._mode)

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def mode(self) -> str:
        return self._mode

    def is_in(self, *states: SessionState) -> bool:
        return self._state in states

    def set_session_id(self, session_id: str) -> None:
        if session_id:
            self.session_id = session_id

    def set_logger(self, logger: object) -> None:
        self._logger = logger

    def switch_mode(self, mode: str, *, reset_to_idle: bool = True) -> bool:
        key = (mode or "").strip().lower()
        if key not in SESSION_MACHINE_PROFILES:
            self._log_warning(
                f"session={self.session_id} mode={self._mode} "
                f"event=switch_mode rejected=unknown_mode detail={key or '-'}"
            )
            return False
        prev = self._mode
        self._mode = key
        self._profile = get_machine_profile(key)
        if reset_to_idle:
            self._state = SessionState.IDLE
        self._log_info(
            f"session={self.session_id} mode={self._mode} "
            f"event=switch_mode from_mode={prev} to_mode={key}"
        )
        return True

    def can(self, event: Union[SessionEvent, str]) -> bool:
        ev = _coerce_event(event)
        if ev == SessionEvent.RESET:
            return True
        allowed = self._profile.event_allowed_from.get(ev)
        if allowed is not None and self._state not in allowed:
            return False
        return (self._state, ev) in self._profile.transitions

    def transition(
        self,
        event: Union[SessionEvent, str],
        *,
        force: bool = False,
        detail: str = "",
    ) -> bool:
        """尝试转移。成功 True；非法则 warn 并 False（RESET 除外）。"""
        ev = _coerce_event(event)
        from_state = self._state

        if ev == SessionEvent.RESET:
            self._apply(from_state, SessionState.IDLE, ev, detail=detail, forced=True)
            return True

        allowed_from = self._profile.event_allowed_from.get(ev)
        if (
            not force
            and allowed_from is not None
            and from_state not in allowed_from
        ):
            self._warn_illegal(from_state, ev, detail, reason="event_not_allowed_from")
            return False

        key = (from_state, ev)
        if key not in self._profile.transitions:
            if force:
                self._warn_illegal(
                    from_state, ev, detail, reason="illegal_transition_forced"
                )
                return False
            self._warn_illegal(from_state, ev, detail, reason="illegal_transition")
            return False

        to_state = self._profile.transitions[key]
        self._apply(from_state, to_state, ev, detail=detail, forced=force)
        return True

    def _apply(
        self,
        from_state: SessionState,
        to_state: SessionState,
        event: SessionEvent,
        *,
        detail: str,
        forced: bool,
    ) -> None:
        self._state = to_state
        msg = (
            f"session={self.session_id} mode={self._mode} event={event.value} "
            f"from={from_state.value} to={to_state.value}"
        )
        if detail:
            msg = f"{msg} detail={detail}"
        if forced:
            msg = f"{msg} forced=1"
        if from_state == to_state:
            self._log_debug(msg)
        else:
            self._log_info(msg)

    def _warn_illegal(
        self,
        from_state: SessionState,
        event: SessionEvent,
        detail: str,
        *,
        reason: str,
    ) -> None:
        msg = (
            f"session={self.session_id} mode={self._mode} event={event.value} "
            f"from={from_state.value} rejected={reason}"
        )
        if detail:
            msg = f"{msg} detail={detail}"
        self._log_warning(msg)

    def _log_info(self, msg: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.info(msg)
        except Exception:
            pass

    def _log_warning(self, msg: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.warning(msg)
        except Exception:
            pass

    def _log_debug(self, msg: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger.debug(msg)
        except Exception:
            pass


# --- micro_service-style soft gates (peer services) ---


def can_start_listen(state: Optional[SessionState]) -> bool:
    """预处理开始收音：IDLE / SPEAKING（打断后听）/ DETECT。"""
    return state in (
        SessionState.IDLE,
        SessionState.SPEAKING,
        SessionState.DETECT,
        SessionState.LISTENING,
    )


def can_send_asr(state: Optional[SessionState]) -> bool:
    """仅 LISTENING 期间向下游送识别结果。"""
    return state == SessionState.LISTENING


def can_start_think(state: Optional[SessionState]) -> bool:
    """Agent 开聊：LISTEN / IDLE / DETECT。"""
    return state in (
        SessionState.LISTENING,
        SessionState.IDLE,
        SessionState.DETECT,
    )


def can_continue_think(state: Optional[SessionState]) -> bool:
    """Agent 续写：THINKING / SPEAKING。"""
    return state in (SessionState.THINKING, SessionState.SPEAKING)


def can_start_play(state: Optional[SessionState]) -> bool:
    """Speaker 开播：THINKING / IDLE（含广播）。"""
    return state in (SessionState.THINKING, SessionState.IDLE, SessionState.SPEAKING)


def can_continue_play(state: Optional[SessionState]) -> bool:
    return state == SessionState.SPEAKING
