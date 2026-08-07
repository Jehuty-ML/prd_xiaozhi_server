"""会话主状态机：统一枚举 + 合法转移 + 结构化转移日志。

同一连接同一时刻只有一个主状态，避免 speaking/abort/收音 多布尔语义靠猜。
非法转移默认拒绝并打 warn（带触发事件）；RESET 可强制回 IDLE。

DETECT：短生命周期唤醒相位，用于轨迹排障与「唤醒后空窗」超时。
ABORT 为事件而非状态（瞬时取消副作用）。

高级参数 mode：默认 common；另有 play_only（仅播放）。
全局 session_state.mode 由 manage-api 设定；智控台「通知更新配置」在
mode 实际变化时向本 WS 实例在线设备广播，并强制打断回 IDLE。
"""

from __future__ import annotations

import asyncio
import time
import uuid
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

# common：(from_state, event) -> to_state；未出现在表中的组合视为非法（RESET 除外）
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

# play_only：仅允许播报相关转移；禁止 DETECT / LISTENING / THINKING / 对话入口
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
    """一套状态机定义（转移表 + 事件入口约束）。"""

    mode: str
    transitions: Dict[Tuple[SessionState, SessionEvent], SessionState]
    event_allowed_from: Dict[SessionEvent, Set[SessionState]] = field(
        default_factory=dict
    )


# 多套状态机注册表。切换：改 manage-api session_state.mode 后「通知更新配置」广播全员。
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

# 兼容旧引用：指向 common 表
LEGAL_TRANSITIONS = _COMMON_TRANSITIONS
EVENT_ALLOWED_FROM = _COMMON_EVENT_ALLOWED_FROM


def get_machine_profile(mode: Optional[str] = None) -> SessionMachineProfile:
    key = (mode or DEFAULT_SESSION_MODE).strip().lower()
    return SESSION_MACHINE_PROFILES.get(
        key, SESSION_MACHINE_PROFILES[DEFAULT_SESSION_MODE]
    )


def resolve_session_mode(config: Optional[Dict[str, Any]] = None) -> str:
    """从配置解析状态机 mode；未知 mode 回退 common。"""
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
    """判断连接或 mode 字符串是否为 play_only。"""
    if isinstance(conn_or_mode, str):
        return conn_or_mode.strip().lower() == PLAY_ONLY_SESSION_MODE
    sm = getattr(conn_or_mode, "session_sm", None)
    if sm is not None:
        return getattr(sm, "mode", "") == PLAY_ONLY_SESSION_MODE
    return (
        resolve_session_mode(getattr(conn_or_mode, "config", None))
        == PLAY_ONLY_SESSION_MODE
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
    return SessionEvent(str(value).lower())


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
        self._mode = resolve_session_mode({"session_state": {"mode": mode or DEFAULT_SESSION_MODE}})
        if mode and mode.strip().lower() in SESSION_MACHINE_PROFILES:
            self._mode = mode.strip().lower()
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
        """切换状态机套件。生产路径：manage-api 改参后「通知更新配置」广播调用。"""
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
        """尝试转移。成功 True；非法则 warn 并 False（RESET / force 除外）。"""
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
    """TTS 结束或打断后清讲话态；已非 SPEAKING 时不触发 TTS_END。

    若处于管理台广播会话，播完/打断后恢复广播前的 mode（默认 common）。
    """
    sm: SessionStateMachine = conn.session_sm
    if sm.state == SessionState.SPEAKING:
        transition_session(conn, SessionEvent.TTS_END, detail="clear_speak")
    else:
        conn.client_is_speaking = False
    _finish_broadcast_speak(conn, detail="clear_speak")


def _finish_broadcast_speak(conn: object, *, detail: str = "") -> None:
    """广播结束：退出临时 play_only，恢复广播前 mode。"""
    if not getattr(conn, "_broadcast_speak_active", False):
        return
    conn._broadcast_speak_active = False
    restore = (
        getattr(conn, "_broadcast_restore_mode", None) or DEFAULT_SESSION_MODE
    )
    conn._broadcast_restore_mode = None

    sm: SessionStateMachine = conn.session_sm
    if sm.mode == restore:
        return

    ok = sm.switch_mode(restore, reset_to_idle=True)
    if not ok:
        return
    cfg = getattr(conn, "config", None)
    if isinstance(cfg, dict):
        block = cfg.get("session_state")
        if not isinstance(block, dict):
            block = {}
            cfg["session_state"] = block
        block["mode"] = restore
    sync_legacy_flags(conn, sm.state)
    logger = getattr(conn, "logger", None)
    if logger is not None:
        try:
            extra = f" detail={detail}" if detail else ""
            logger.info(
                f"广播结束，session_state.mode 已恢复为 {restore}{extra}"
            )
        except Exception:
            pass


def speak_play_only_denied(conn: object) -> None:
    """play_only 下听到唤醒词/试图对话时，播报降级话术。"""
    if getattr(conn, "stop_event", None) and conn.stop_event.is_set():
        return
    if not getattr(conn, "tts", None):
        return
    text = get_play_only_deny_text(getattr(conn, "config", None))
    conn.sentence_id = uuid.uuid4().hex
    conn.client_abort = False
    transition_session(conn, SessionEvent.TTS_START, detail="play_only_deny")
    try:
        from core.handle.intentHandler import speak_txt

        speak_txt(conn, text)
    except Exception as e:
        logger = getattr(conn, "logger", None)
        if logger is not None:
            try:
                logger.warning(f"play_only 降级播报失败: {e}")
            except Exception:
                pass


async def apply_session_mode(
    conn: object,
    mode: str,
    *,
    force_interrupt: bool = True,
) -> bool:
    """将连接切换到指定 session_state.mode。

    mode 未变化时只同步配置、不打断、不 reset。
    mode 变化且设备在线、force_interrupt 时：打断当前对话/播放并 RESET 到 IDLE，再切 mode。
    """
    resolved = (mode or "").strip().lower()
    if resolved not in SESSION_MACHINE_PROFILES:
        resolved = DEFAULT_SESSION_MODE

    cfg = getattr(conn, "config", None)
    if isinstance(cfg, dict):
        block = cfg.get("session_state")
        if not isinstance(block, dict):
            block = {}
            cfg["session_state"] = block
        block["mode"] = resolved

    sm: SessionStateMachine = conn.session_sm
    mode_changed = sm.mode != resolved
    if not mode_changed:
        # 同 mode 热更新（如无关配置刷新）不得打断进行中的对话
        sync_legacy_flags(conn, sm.state)
        return True

    need_interrupt = force_interrupt and (
        sm.state != SessionState.IDLE
        or getattr(conn, "client_is_speaking", False)
    )
    closed = getattr(conn, "_closed", False)
    ws = getattr(conn, "websocket", None)
    online = ws is not None and not closed

    if need_interrupt and online:
        try:
            from core.handle.abortHandle import handleAbortMessage

            await handleAbortMessage(conn)
        except Exception:
            conn.client_abort = True
            if hasattr(conn, "clear_queues"):
                try:
                    conn.clear_queues()
                except Exception:
                    pass
            sm.transition(SessionEvent.RESET, detail="mode_switch")
            clear_speak_status(conn)

    ok = sm.switch_mode(resolved, reset_to_idle=True)
    if ok:
        sync_legacy_flags(conn, sm.state)
        conn.just_woken_up = False
    return ok


async def speak_broadcast_text(conn: object, text: str) -> bool:
    """管理台广播播报：自动切 play_only → TTS → 播完/打断后恢复原 mode。

    无需事先改全局 session_state.mode（可保持 common）；若原本已是
    play_only，播完仍保持 play_only，不会被误恢复成 common。
    """
    content = (text or "").strip()
    if not content:
        return False
    if getattr(conn, "stop_event", None) and conn.stop_event.is_set():
        return False
    if not getattr(conn, "tts", None):
        return False

    closed = getattr(conn, "_closed", False)
    ws = getattr(conn, "websocket", None)
    if ws is None or closed:
        return False

    sm: SessionStateMachine = conn.session_sm
    conn._broadcast_restore_mode = sm.mode or DEFAULT_SESSION_MODE
    # 先切 mode（内部可能 abort→clearSpeak）；标志要在之后再置，避免被清掉
    try:
        if sm.mode != PLAY_ONLY_SESSION_MODE:
            ok_mode = await apply_session_mode(
                conn, PLAY_ONLY_SESSION_MODE, force_interrupt=True
            )
            if not ok_mode:
                conn._broadcast_restore_mode = None
                return False
        elif sm.state != SessionState.IDLE or getattr(
            conn, "client_is_speaking", False
        ):
            # 已是 play_only：仍打断当前播报，播完后保持 play_only
            try:
                from core.handle.abortHandle import handleAbortMessage

                await handleAbortMessage(conn)
            except Exception:
                conn.client_abort = True
                if hasattr(conn, "clear_queues"):
                    try:
                        conn.clear_queues()
                    except Exception:
                        pass
                sm.transition(SessionEvent.RESET, detail="broadcast_barge_in")
                clear_speak_status(conn)

        conn._broadcast_speak_active = True
        conn.client_abort = False
        conn.sentence_id = uuid.uuid4().hex
        if not transition_session(
            conn, SessionEvent.TTS_START, detail="broadcast_speak"
        ):
            _finish_broadcast_speak(conn, detail="tts_start_rejected")
            return False

        from core.handle.intentHandler import speak_txt

        speak_txt(conn, content)
        return True
    except Exception as e:
        _finish_broadcast_speak(conn, detail="speak_failed")
        logger = getattr(conn, "logger", None)
        if logger is not None:
            try:
                logger.warning(f"广播播报失败: {e}")
            except Exception:
                pass
        return False
