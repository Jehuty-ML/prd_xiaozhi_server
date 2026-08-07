"""会话主状态机：统一枚举 + 合法转移 + 结构化转移日志。

同一连接同一时刻只有一个主状态，避免 speaking/abort/收音 多布尔语义靠猜。
非法转移默认拒绝并打 warn（带触发事件）；RESET 可强制回 IDLE。

DETECT：短生命周期唤醒相位，用于轨迹排障与「唤醒后空窗」超时。
ABORT 为事件而非状态（瞬时取消副作用）。
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Dict, Optional, Set, Tuple, Union


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


# (from_state, event) -> to_state；未出现在表中的组合视为非法（RESET 除外）
LEGAL_TRANSITIONS: Dict[Tuple[SessionState, SessionEvent], SessionState] = {
    # 空闲
    (SessionState.IDLE, SessionEvent.DETECT): SessionState.DETECT,
    (SessionState.IDLE, SessionEvent.LISTEN_START): SessionState.LISTENING,
    (SessionState.IDLE, SessionEvent.CHAT_START): SessionState.THINKING,
    (SessionState.IDLE, SessionEvent.VOICE_END): SessionState.THINKING,
    (SessionState.IDLE, SessionEvent.IDLE_TIMEOUT): SessionState.THINKING,
    (SessionState.IDLE, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.IDLE, SessionEvent.ABORT): SessionState.IDLE,
    # 唤醒（短相位）
    (SessionState.DETECT, SessionEvent.DETECT): SessionState.DETECT,
    (SessionState.DETECT, SessionEvent.LISTEN_START): SessionState.LISTENING,
    (SessionState.DETECT, SessionEvent.CHAT_START): SessionState.THINKING,
    (SessionState.DETECT, SessionEvent.VOICE_END): SessionState.THINKING,
    (SessionState.DETECT, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.DETECT, SessionEvent.DETECT_TIMEOUT): SessionState.IDLE,
    (SessionState.DETECT, SessionEvent.ABORT): SessionState.IDLE,
    # 聆听
    (SessionState.LISTENING, SessionEvent.DETECT): SessionState.DETECT,
    (SessionState.LISTENING, SessionEvent.LISTEN_START): SessionState.LISTENING,
    (SessionState.LISTENING, SessionEvent.VOICE_END): SessionState.THINKING,
    (SessionState.LISTENING, SessionEvent.CHAT_START): SessionState.THINKING,
    (SessionState.LISTENING, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.LISTENING, SessionEvent.TTS_END): SessionState.LISTENING,
    (SessionState.LISTENING, SessionEvent.ABORT): SessionState.IDLE,
    # 思考（LLM）
    (SessionState.THINKING, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.THINKING, SessionEvent.ABORT): SessionState.IDLE,
    (SessionState.THINKING, SessionEvent.CHAT_START): SessionState.THINKING,
    (SessionState.THINKING, SessionEvent.VOICE_END): SessionState.THINKING,
    (SessionState.THINKING, SessionEvent.TTS_END): SessionState.IDLE,
    # 播报（仅 TTS 开始后进入；结束/打断回 IDLE）
    (SessionState.SPEAKING, SessionEvent.TTS_START): SessionState.SPEAKING,
    (SessionState.SPEAKING, SessionEvent.TTS_END): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.ABORT): SessionState.IDLE,
    (SessionState.SPEAKING, SessionEvent.LISTEN_START): SessionState.LISTENING,
    (SessionState.SPEAKING, SessionEvent.DETECT): SessionState.DETECT,
}

# 额外语义：事件只能从这些状态触发（比转移表更严的入口约束）
# ABORT：任意主状态均可打断回 IDLE（含幂等 IDLE→IDLE）
EVENT_ALLOWED_FROM: Dict[SessionEvent, Set[SessionState]] = {
    SessionEvent.IDLE_TIMEOUT: {SessionState.IDLE},
    SessionEvent.DETECT_TIMEOUT: {SessionState.DETECT},
}


def _coerce_event(value: Union[SessionEvent, str]) -> SessionEvent:
    if isinstance(value, SessionEvent):
        return value
    return SessionEvent(str(value).lower())


class SessionStateMachine:
    """连接级主状态机。"""

    def __init__(
        self,
        session_id: str = "",
        initial: SessionState = SessionState.IDLE,
        logger: Optional[object] = None,
    ):
        self.session_id = session_id or "-"
        self._state = initial
        self._logger = logger

    @property
    def state(self) -> SessionState:
        return self._state

    def is_in(self, *states: SessionState) -> bool:
        return self._state in states

    def set_session_id(self, session_id: str) -> None:
        if session_id:
            self.session_id = session_id

    def set_logger(self, logger: object) -> None:
        self._logger = logger

    def can(self, event: Union[SessionEvent, str]) -> bool:
        ev = _coerce_event(event)
        if ev == SessionEvent.RESET:
            return True
        allowed = EVENT_ALLOWED_FROM.get(ev)
        if allowed is not None and self._state not in allowed:
            return False
        return (self._state, ev) in LEGAL_TRANSITIONS

    def transition(
        self,
        event: Union[SessionEvent, str],
        *,
        force: bool = False,
        detail: str = "",
    ) -> bool:
        """尝试转移。成功 True；非法则 warn 并 False（RESET / force 除外）。"""
        ev = _coerce_event(event)
        from_state = self._state

        if ev == SessionEvent.RESET:
            self._apply(from_state, SessionState.IDLE, ev, detail=detail, forced=True)
            return True

        allowed_from = EVENT_ALLOWED_FROM.get(ev)
        if (
            not force
            and allowed_from is not None
            and from_state not in allowed_from
        ):
            self._warn_illegal(from_state, ev, detail, reason="event_not_allowed_from")
            return False

        key = (from_state, ev)
        if key not in LEGAL_TRANSITIONS:
            if force:
                self._warn_illegal(
                    from_state, ev, detail, reason="illegal_transition_forced"
                )
                return False
            self._warn_illegal(from_state, ev, detail, reason="illegal_transition")
            return False

        to_state = LEGAL_TRANSITIONS[key]
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
            f"session={self.session_id} event={event.value} "
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
            f"session={self.session_id} event={event.value} "
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


def sync_legacy_flags(conn: object, state: SessionState) -> None:
    """把主状态同步到历史布尔字段，降低改动面。"""
    if hasattr(conn, "client_is_speaking"):
        conn.client_is_speaking = state == SessionState.SPEAKING


def transition_session(
    conn: object,
    event: Union[SessionEvent, str],
    *,
    detail: str = "",
    force: bool = False,
) -> bool:
    """连接级转移：改主状态、同步 legacy 布尔，并管理 DETECT 空窗任务。"""
    sm: SessionStateMachine = conn.session_sm
    prev = sm.state
    ok = sm.transition(event, detail=detail, force=force)
    if not ok:
        return False
    sync_legacy_flags(conn, sm.state)
    ev = event if isinstance(event, SessionEvent) else SessionEvent(str(event).lower())
    if sm.state == SessionState.DETECT:
        if prev != SessionState.DETECT or ev == SessionEvent.DETECT:
            arm_detect_timeout(conn)
    elif prev == SessionState.DETECT:
        cancel_detect_timeout(conn)
    return True


def enter_detect(conn: object, detail: str = "") -> bool:
    """进入唤醒 DETECT 相位，并开启空窗超时。"""
    ok = transition_session(conn, SessionEvent.DETECT, detail=detail or "wakeup")
    if ok:
        conn.just_woken_up = True
        conn.detect_entered_at = time.time()
    return ok


def cancel_detect_timeout(conn: object) -> None:
    task = getattr(conn, "_detect_timeout_task", None)
    if task and not task.done():
        try:
            current = asyncio.current_task()
        except Exception:
            current = None
        # watchdog 自身触发 DETECT_TIMEOUT 时不要 cancel 自己
        if task is not current:
            task.cancel()
    conn._detect_timeout_task = None


def arm_detect_timeout(conn: object) -> None:
    """武装 DETECT 空窗超时（仅当前已在 DETECT 且有 event loop 时）。"""
    cancel_detect_timeout(conn)
    sm: SessionStateMachine = conn.session_sm
    if not sm.is_in(SessionState.DETECT):
        return
    loop = getattr(conn, "loop", None)
    if loop is None:
        return

    coro = detect_timeout_watchdog(conn)
    try:
        spawn = getattr(conn, "spawn_task", None)
        if callable(spawn):
            conn._detect_timeout_task = spawn(coro)
            return
    except Exception:
        pass
    try:
        conn._detect_timeout_task = asyncio.ensure_future(coro)
    except Exception:
        conn._detect_timeout_task = None


async def detect_timeout_watchdog(conn: object):
    """唤醒后无人说话：DETECT → IDLE。"""
    try:
        cfg = getattr(conn, "config", {}) or {}
        timeout = float(cfg.get("detect_timeout_seconds", 10))
        if timeout <= 0:
            return
        await asyncio.sleep(timeout)
        sm: SessionStateMachine = conn.session_sm
        if not sm.is_in(SessionState.DETECT):
            return
        if transition_session(conn, SessionEvent.DETECT_TIMEOUT, detail="no_speech"):
            conn.just_woken_up = False
            logger = getattr(conn, "logger", None)
            if logger is not None:
                try:
                    logger.info(f"DETECT 空窗超时({timeout}s)，回 IDLE")
                except Exception:
                    pass
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger = getattr(conn, "logger", None)
        if logger is not None:
            try:
                logger.warning(f"DETECT 超时任务异常: {e}")
            except Exception:
                pass

def clear_speak_status(conn: object) -> None:
    """TTS 结束或打断后清讲话态；已非 SPEAKING 时不触发 TTS_END。"""
    sm: SessionStateMachine = conn.session_sm
    if sm.state == SessionState.SPEAKING:
        transition_session(conn, SessionEvent.TTS_END, detail="clear_speak")
    else:
        conn.client_is_speaking = False
