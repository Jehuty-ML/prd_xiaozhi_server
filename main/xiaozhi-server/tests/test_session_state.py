#!/usr/bin/env python3
"""会话状态机单测：合法转移 / 非法拒绝 / 结构化日志 + 连接接线。"""

from __future__ import annotations

import asyncio
import unittest
from typing import List

from core.utils.session_state import (
    DEFAULT_SESSION_MODE,
    LEGAL_TRANSITIONS,
    PLAY_ONLY_SESSION_MODE,
    SESSION_MACHINE_PROFILES,
    SessionEvent,
    SessionState,
    SessionStateMachine,
    apply_session_mode,
    clear_speak_status,
    enter_detect,
    is_play_only_mode,
    resolve_session_mode,
    speak_broadcast_text,
    sync_legacy_flags,
    transition_session,
)


class _MemLog:
    def __init__(self):
        self.info_msgs: List[str] = []
        self.warn_msgs: List[str] = []
        self.debug_msgs: List[str] = []

    def info(self, msg: str):
        self.info_msgs.append(msg)

    def warning(self, msg: str):
        self.warn_msgs.append(msg)

    def debug(self, msg: str):
        self.debug_msgs.append(msg)


class _FakeConn:
    """轻量连接桩：覆盖 transition_session / DETECT 超时 / clear_speak 接线。"""

    def __init__(self, detect_timeout_seconds: float = 0.05):
        self.config = {"detect_timeout_seconds": detect_timeout_seconds}
        self.logger = _MemLog()
        self.session_sm = SessionStateMachine(session_id="c1", logger=self.logger)
        self.client_is_speaking = False
        self.just_woken_up = False
        self.detect_entered_at = 0.0
        self._detect_timeout_task = None
        self.loop = None
        self._tracked: list = []

    def spawn_task(self, coro):
        task = self.loop.create_task(coro)
        self._tracked.append(task)
        return task


class SessionStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.log = _MemLog()
        self.sm = SessionStateMachine(session_id="s1", logger=self.log)

    def test_initial_idle(self):
        self.assertEqual(self.sm.state, SessionState.IDLE)
        self.assertEqual(self.sm.mode, DEFAULT_SESSION_MODE)

    def test_resolve_session_mode(self):
        self.assertEqual(resolve_session_mode({}), DEFAULT_SESSION_MODE)
        self.assertEqual(
            resolve_session_mode({"session_state": {"mode": "common"}}),
            "common",
        )
        self.assertEqual(
            resolve_session_mode({"session_state": {"mode": "play_only"}}),
            PLAY_ONLY_SESSION_MODE,
        )
        self.assertEqual(
            resolve_session_mode({"session_state.mode": "common"}),
            "common",
        )
        # 未知 mode 回退 common
        self.assertEqual(
            resolve_session_mode({"session_state": {"mode": "game"}}),
            DEFAULT_SESSION_MODE,
        )

    def test_switch_mode_play_only_and_unknown(self):
        self.assertFalse(self.sm.switch_mode("game"))
        self.assertEqual(self.sm.mode, DEFAULT_SESSION_MODE)
        self.assertTrue(
            any("rejected=unknown_mode" in m for m in self.log.warn_msgs)
        )
        self.assertTrue(self.sm.switch_mode(PLAY_ONLY_SESSION_MODE))
        self.assertEqual(self.sm.mode, PLAY_ONLY_SESSION_MODE)
        self.assertEqual(self.sm.state, SessionState.IDLE)
        # play_only 禁止聆听/唤醒/对话入口
        self.assertFalse(self.sm.transition(SessionEvent.LISTEN_START))
        self.assertFalse(self.sm.transition(SessionEvent.DETECT))
        self.assertFalse(self.sm.transition(SessionEvent.CHAT_START))
        self.assertFalse(self.sm.transition(SessionEvent.VOICE_END))
        self.assertFalse(self.sm.transition(SessionEvent.IDLE_TIMEOUT))
        # 主路径：IDLE + tts_start → SPEAKING
        self.assertTrue(self.sm.transition(SessionEvent.TTS_START))
        self.assertEqual(self.sm.state, SessionState.SPEAKING)
        # 重入：SPEAKING + tts_start → SPEAKING（新一轮播报）
        self.assertTrue(self.sm.transition(SessionEvent.TTS_START))
        self.assertEqual(self.sm.state, SessionState.SPEAKING)
        self.assertTrue(self.sm.transition(SessionEvent.TTS_END))
        self.assertEqual(self.sm.state, SessionState.IDLE)
        self.assertTrue(is_play_only_mode(self.sm.mode))

    def test_play_only_legal_transition_matrix(self):
        """play_only 合法转移矩阵（含 IDLE→SPEAKING 主路径与 SPEAKING 重入）。"""
        profile = SESSION_MACHINE_PROFILES[PLAY_ONLY_SESSION_MODE]
        expected = {
            (SessionState.IDLE, SessionEvent.TTS_START): SessionState.SPEAKING,
            (SessionState.IDLE, SessionEvent.ABORT): SessionState.IDLE,
            (SessionState.SPEAKING, SessionEvent.TTS_START): SessionState.SPEAKING,
            (SessionState.SPEAKING, SessionEvent.TTS_END): SessionState.IDLE,
            (SessionState.SPEAKING, SessionEvent.ABORT): SessionState.IDLE,
        }
        self.assertEqual(dict(profile.transitions), expected)
        # 超时类事件入口为空：一律拒绝
        self.assertEqual(profile.event_allowed_from[SessionEvent.IDLE_TIMEOUT], set())
        self.assertEqual(profile.event_allowed_from[SessionEvent.DETECT_TIMEOUT], set())

        sm = SessionStateMachine(
            session_id="po-matrix", logger=self.log, mode=PLAY_ONLY_SESSION_MODE
        )
        # IDLE → SPEAKING（开播）
        self.assertTrue(sm.transition(SessionEvent.TTS_START, detail="open"))
        self.assertEqual(sm.state, SessionState.SPEAKING)
        joined = " | ".join(self.log.info_msgs)
        self.assertIn(
            "mode=play_only event=tts_start from=IDLE to=SPEAKING", joined
        )
        # SPEAKING → SPEAKING（重入）
        self.assertTrue(sm.transition(SessionEvent.TTS_START, detail="reenter"))
        self.assertEqual(sm.state, SessionState.SPEAKING)
        # SPEAKING → IDLE（结束）
        self.assertTrue(sm.transition(SessionEvent.TTS_END))
        self.assertEqual(sm.state, SessionState.IDLE)
        # IDLE abort 幂等
        self.assertTrue(sm.transition(SessionEvent.ABORT))
        self.assertEqual(sm.state, SessionState.IDLE)

    def test_happy_path_production(self):
        """生产路径：listen → chat_start → tts_start → speaking → tts_end。

        VOICE_END 在表内合法，但 handler 当前未接线，见 test_voice_end_table_only。
        """
        self.assertTrue(self.sm.transition(SessionEvent.LISTEN_START))
        self.assertEqual(self.sm.state, SessionState.LISTENING)
        self.assertTrue(self.sm.transition(SessionEvent.CHAT_START))
        self.assertEqual(self.sm.state, SessionState.THINKING)
        self.assertTrue(self.sm.transition(SessionEvent.TTS_START))
        self.assertEqual(self.sm.state, SessionState.SPEAKING)
        self.assertTrue(self.sm.transition(SessionEvent.TTS_END))
        self.assertEqual(self.sm.state, SessionState.IDLE)

        joined = " | ".join(self.log.info_msgs)
        self.assertIn(
            "session=s1 mode=common event=listen_start from=IDLE to=LISTENING", joined
        )
        self.assertIn("event=chat_start from=LISTENING to=THINKING", joined)
        self.assertIn("event=tts_start from=THINKING to=SPEAKING", joined)
        self.assertIn("event=tts_end from=SPEAKING to=IDLE", joined)

    def test_voice_end_table_only(self):
        """VOICE_END：表内合法边，当前未接线到 handler。"""
        self.assertIn(
            (SessionState.LISTENING, SessionEvent.VOICE_END), LEGAL_TRANSITIONS
        )
        self.assertEqual(
            LEGAL_TRANSITIONS[(SessionState.LISTENING, SessionEvent.VOICE_END)],
            SessionState.THINKING,
        )
        self.sm.transition(SessionEvent.LISTEN_START)
        self.assertTrue(self.sm.transition(SessionEvent.VOICE_END))
        self.assertEqual(self.sm.state, SessionState.THINKING)

    def test_speaking_to_listen_start(self):
        """生产边：播完/听模式切回 — SPEAKING → LISTEN_START → LISTENING。"""
        self.sm.transition(SessionEvent.CHAT_START)
        self.sm.transition(SessionEvent.TTS_START)
        self.assertEqual(self.sm.state, SessionState.SPEAKING)
        self.assertTrue(self.sm.transition(SessionEvent.LISTEN_START))
        self.assertEqual(self.sm.state, SessionState.LISTENING)

    def test_idle_timeout_then_chat_start_idempotent(self):
        self.assertTrue(self.sm.transition(SessionEvent.IDLE_TIMEOUT))
        self.assertEqual(self.sm.state, SessionState.THINKING)
        # startToChat 在 IDLE_TIMEOUT 之后仍会 CHAT_START（幂等）
        self.assertTrue(self.sm.transition(SessionEvent.CHAT_START))
        self.assertEqual(self.sm.state, SessionState.THINKING)

    def test_abort_from_any_state_to_idle(self):
        self.assertTrue(self.sm.transition(SessionEvent.ABORT))
        self.assertEqual(self.sm.state, SessionState.IDLE)

        for enter, leave_msg in (
            (SessionEvent.DETECT, "from=DETECT to=IDLE"),
            (SessionEvent.LISTEN_START, "from=LISTENING to=IDLE"),
            (SessionEvent.CHAT_START, "from=THINKING to=IDLE"),
        ):
            self.sm.transition(SessionEvent.RESET)
            self.sm.transition(enter)
            self.assertTrue(self.sm.transition(SessionEvent.ABORT, detail="client"))
            self.assertEqual(self.sm.state, SessionState.IDLE)
            self.assertTrue(
                any(leave_msg in m for m in self.log.info_msgs),
                msg=leave_msg,
            )

        self.sm.transition(SessionEvent.CHAT_START)
        self.sm.transition(SessionEvent.TTS_START)
        self.assertTrue(self.sm.transition(SessionEvent.ABORT, detail="client"))
        self.assertEqual(self.sm.state, SessionState.IDLE)
        self.assertTrue(
            any(
                "event=abort from=SPEAKING to=IDLE detail=client" in m
                for m in self.log.info_msgs
            )
        )

    def test_idle_timeout_only_in_idle(self):
        self.sm.transition(SessionEvent.LISTEN_START)
        self.assertFalse(self.sm.transition(SessionEvent.IDLE_TIMEOUT))
        self.assertEqual(self.sm.state, SessionState.LISTENING)

        self.sm.transition(SessionEvent.RESET)
        self.assertTrue(self.sm.transition(SessionEvent.IDLE_TIMEOUT))
        self.assertEqual(self.sm.state, SessionState.THINKING)

    def test_cannot_enter_speaking_without_tts_start(self):
        for (_frm, ev), to in LEGAL_TRANSITIONS.items():
            if to == SessionState.SPEAKING:
                self.assertEqual(ev, SessionEvent.TTS_START)

    def test_illegal_transition_rejected(self):
        self.assertFalse(self.sm.transition(SessionEvent.TTS_END))
        self.assertEqual(self.sm.state, SessionState.IDLE)
        self.assertTrue(
            any("rejected=illegal_transition" in m for m in self.log.warn_msgs)
        )

    def test_reset_always_works(self):
        self.sm.transition(SessionEvent.CHAT_START)
        self.sm.transition(SessionEvent.TTS_START)
        self.assertTrue(self.sm.transition(SessionEvent.RESET))
        self.assertEqual(self.sm.state, SessionState.IDLE)

    def test_sync_legacy_flags(self):
        class C:
            client_is_speaking = False

        c = C()
        sync_legacy_flags(c, SessionState.SPEAKING)
        self.assertTrue(c.client_is_speaking)
        sync_legacy_flags(c, SessionState.IDLE)
        self.assertFalse(c.client_is_speaking)

    def test_can_helper(self):
        self.assertTrue(self.sm.can(SessionEvent.LISTEN_START))
        self.assertTrue(self.sm.can(SessionEvent.ABORT))
        self.sm.transition(SessionEvent.CHAT_START)
        self.assertTrue(self.sm.can(SessionEvent.ABORT))
        self.assertTrue(self.sm.can(SessionEvent.TTS_START))

    def test_detect_happy_path_and_timeout(self):
        self.assertTrue(self.sm.transition(SessionEvent.DETECT, detail="wakeup"))
        self.assertEqual(self.sm.state, SessionState.DETECT)
        self.assertTrue(
            any(
                "event=detect from=IDLE to=DETECT detail=wakeup" in m
                for m in self.log.info_msgs
            )
        )

        self.assertTrue(self.sm.transition(SessionEvent.CHAT_START))
        self.assertEqual(self.sm.state, SessionState.THINKING)

        self.sm.transition(SessionEvent.RESET)
        self.sm.transition(SessionEvent.DETECT)
        self.assertFalse(self.sm.can(SessionEvent.IDLE_TIMEOUT))
        self.assertTrue(self.sm.transition(SessionEvent.DETECT_TIMEOUT))
        self.assertEqual(self.sm.state, SessionState.IDLE)
        self.assertTrue(
            any(
                "event=detect_timeout from=DETECT to=IDLE" in m
                for m in self.log.info_msgs
            )
        )

    def test_detect_timeout_only_in_detect(self):
        self.assertFalse(self.sm.transition(SessionEvent.DETECT_TIMEOUT))
        self.assertTrue(
            any("rejected=event_not_allowed_from" in m for m in self.log.warn_msgs)
        )

    def test_detect_to_listening(self):
        self.sm.transition(SessionEvent.DETECT)
        self.assertTrue(self.sm.transition(SessionEvent.LISTEN_START))
        self.assertEqual(self.sm.state, SessionState.LISTENING)

    def test_detect_to_speaking_via_tts(self):
        self.sm.transition(SessionEvent.DETECT)
        self.assertTrue(self.sm.transition(SessionEvent.TTS_START))
        self.assertEqual(self.sm.state, SessionState.SPEAKING)


class SessionConnectionWiringTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 transition_session / DETECT watchdog / abort 后 clearSpeakStatus。"""

    async def asyncSetUp(self):
        self.loop = asyncio.get_running_loop()
        self.conn = _FakeConn(detect_timeout_seconds=0.05)
        self.conn.loop = self.loop

    async def asyncTearDown(self):
        for t in list(self.conn._tracked):
            if not t.done():
                t.cancel()
        if self.conn._tracked:
            await asyncio.gather(*self.conn._tracked, return_exceptions=True)

    async def test_transition_session_syncs_speaking_flag(self):
        self.assertTrue(
            transition_session(self.conn, SessionEvent.CHAT_START, detail="start_to_chat")
        )
        self.assertFalse(self.conn.client_is_speaking)
        self.assertTrue(
            transition_session(self.conn, SessionEvent.TTS_START, detail="tts")
        )
        self.assertEqual(self.conn.session_sm.state, SessionState.SPEAKING)
        self.assertTrue(self.conn.client_is_speaking)
        self.assertTrue(transition_session(self.conn, SessionEvent.TTS_END))
        self.assertFalse(self.conn.client_is_speaking)

    async def test_abort_then_clear_speak_does_not_tts_end(self):
        transition_session(self.conn, SessionEvent.CHAT_START)
        transition_session(self.conn, SessionEvent.TTS_START)
        self.assertTrue(self.conn.client_is_speaking)

        self.assertTrue(transition_session(self.conn, SessionEvent.ABORT, detail="client"))
        self.assertEqual(self.conn.session_sm.state, SessionState.IDLE)
        # abort 已到 IDLE；再 clear 不应触发 TTS_END（无 illegal warn）
        before_warn = len(self.conn.logger.warn_msgs)
        clear_speak_status(self.conn)
        self.assertEqual(self.conn.session_sm.state, SessionState.IDLE)
        self.assertFalse(self.conn.client_is_speaking)
        self.assertEqual(len(self.conn.logger.warn_msgs), before_warn)
        self.assertFalse(
            any("tts_end" in m for m in self.conn.logger.info_msgs[-3:]),
        )

    async def test_detect_timeout_watchdog_to_idle(self):
        self.assertTrue(enter_detect(self.conn, detail="wakeup_no_greeting"))
        self.assertEqual(self.conn.session_sm.state, SessionState.DETECT)
        self.assertTrue(self.conn.just_woken_up)
        self.assertIsNotNone(self.conn._detect_timeout_task)

        await asyncio.wait_for(self.conn._detect_timeout_task, timeout=1.0)
        self.assertEqual(self.conn.session_sm.state, SessionState.IDLE)
        self.assertFalse(self.conn.just_woken_up)
        self.assertTrue(
            any("detect_timeout" in m for m in self.conn.logger.info_msgs)
        )

    async def test_leave_detect_cancels_watchdog(self):
        enter_detect(self.conn, detail="wakeup")
        task = self.conn._detect_timeout_task
        self.assertIsNotNone(task)
        transition_session(self.conn, SessionEvent.CHAT_START)
        self.assertEqual(self.conn.session_sm.state, SessionState.THINKING)
        # 离开 DETECT 应取消超时任务
        await asyncio.sleep(0)
        self.assertTrue(task.cancelled() or task.done())
        self.assertIsNone(self.conn._detect_timeout_task)

    async def test_apply_session_mode_force_interrupt_to_idle(self):
        """切换 mode 时强制打断并回 IDLE（模拟在线连接）。"""
        transition_session(self.conn, SessionEvent.CHAT_START)
        transition_session(self.conn, SessionEvent.TTS_START)
        self.assertEqual(self.conn.session_sm.state, SessionState.SPEAKING)

        self.conn.websocket = object()
        self.conn._closed = False
        self.conn.clear_queues = lambda: None

        ok = await apply_session_mode(
            self.conn, PLAY_ONLY_SESSION_MODE, force_interrupt=True
        )
        self.assertTrue(ok)
        self.assertEqual(self.conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)
        self.assertEqual(self.conn.session_sm.state, SessionState.IDLE)
        self.assertFalse(self.conn.client_is_speaking)
        self.assertEqual(
            self.conn.config.get("session_state", {}).get("mode"),
            PLAY_ONLY_SESSION_MODE,
        )

    async def test_apply_session_mode_same_mode_does_not_interrupt(self):
        """同 mode 热更新不得打断进行中的对话。"""
        transition_session(self.conn, SessionEvent.CHAT_START)
        transition_session(self.conn, SessionEvent.TTS_START)
        self.assertEqual(self.conn.session_sm.state, SessionState.SPEAKING)

        self.conn.websocket = object()
        self.conn._closed = False
        cleared = {"n": 0}
        self.conn.clear_queues = lambda: cleared.__setitem__("n", cleared["n"] + 1)

        ok = await apply_session_mode(
            self.conn, DEFAULT_SESSION_MODE, force_interrupt=True
        )
        self.assertTrue(ok)
        self.assertEqual(cleared["n"], 0)
        self.assertEqual(self.conn.session_sm.mode, DEFAULT_SESSION_MODE)
        self.assertEqual(self.conn.session_sm.state, SessionState.SPEAKING)
        self.assertTrue(self.conn.client_is_speaking)
    async def test_broadcast_speak_auto_play_only_then_restore_common(self):
        """广播会话：play_only 期间禁对话，clear_speak 后回 common。"""
        self.conn.websocket = object()
        self.conn._closed = False
        self.conn.clear_queues = lambda: None
        self.conn.config = {"session_state": {"mode": "common"}}

        ok = await apply_session_mode(
            self.conn, PLAY_ONLY_SESSION_MODE, force_interrupt=True
        )
        self.assertTrue(ok)
        self.conn._broadcast_speak_active = True
        self.conn._broadcast_restore_mode = DEFAULT_SESSION_MODE
        self.assertTrue(
            transition_session(
                self.conn, SessionEvent.TTS_START, detail="broadcast_speak"
            )
        )
        self.assertEqual(self.conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)
        self.assertEqual(self.conn.session_sm.state, SessionState.SPEAKING)
        # 广播期间禁止聆听
        self.assertFalse(
            self.conn.session_sm.transition(SessionEvent.LISTEN_START)
        )

        clear_speak_status(self.conn)
        self.assertFalse(getattr(self.conn, "_broadcast_speak_active", False))
        self.assertEqual(self.conn.session_sm.mode, DEFAULT_SESSION_MODE)
        self.assertEqual(self.conn.session_sm.state, SessionState.IDLE)
        self.assertEqual(
            self.conn.config.get("session_state", {}).get("mode"),
            DEFAULT_SESSION_MODE,
        )

    async def test_broadcast_speak_preserves_permanent_play_only(self):
        """永久 play_only 下广播播完后不得被恢复成 common。"""
        self.conn.websocket = object()
        self.conn._closed = False
        self.conn.clear_queues = lambda: None
        self.conn.config = {"session_state": {"mode": PLAY_ONLY_SESSION_MODE}}
        self.conn.session_sm.switch_mode(PLAY_ONLY_SESSION_MODE, reset_to_idle=True)

        self.conn._broadcast_speak_active = True
        self.conn._broadcast_restore_mode = PLAY_ONLY_SESSION_MODE
        self.assertTrue(
            transition_session(
                self.conn, SessionEvent.TTS_START, detail="broadcast_speak"
            )
        )
        clear_speak_status(self.conn)
        self.assertFalse(getattr(self.conn, "_broadcast_speak_active", False))
        self.assertEqual(self.conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)
        self.assertEqual(self.conn.session_sm.state, SessionState.IDLE)
        self.assertEqual(
            self.conn.config.get("session_state", {}).get("mode"),
            PLAY_ONLY_SESSION_MODE,
        )

    async def test_speak_broadcast_text_saves_prior_mode(self):
        """speak_broadcast_text 应把恢复 mode 记为切换前的值。"""
        self.conn.websocket = object()
        self.conn._closed = False
        self.conn.clear_queues = lambda: None
        self.conn.config = {"session_state": {"mode": PLAY_ONLY_SESSION_MODE}}
        self.conn.session_sm.switch_mode(PLAY_ONLY_SESSION_MODE, reset_to_idle=True)
        self.conn.tts = object()

        spoken = {"text": None}

        def _fake_speak(conn, text):
            spoken["text"] = text

        import sys
        import types

        fake_intent = types.ModuleType("core.handle.intentHandler")
        fake_intent.speak_txt = _fake_speak
        # speak_broadcast_text 延迟 import；注入轻量假模块避免拉 opus 依赖
        prev = sys.modules.get("core.handle.intentHandler")
        sys.modules["core.handle.intentHandler"] = fake_intent
        try:
            ok = await speak_broadcast_text(self.conn, "全体注意")
            self.assertTrue(ok)
            self.assertEqual(
                self.conn._broadcast_restore_mode, PLAY_ONLY_SESSION_MODE
            )
            self.assertTrue(self.conn._broadcast_speak_active)
            self.assertEqual(spoken["text"], "全体注意")
            self.assertEqual(self.conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)
            self.assertEqual(self.conn.session_sm.state, SessionState.SPEAKING)
        finally:
            if prev is None:
                sys.modules.pop("core.handle.intentHandler", None)
            else:
                sys.modules["core.handle.intentHandler"] = prev

    async def test_overlapping_broadcast_preserves_restore_mode(self):
        """重叠广播不得把 restore 覆盖成临时 play_only，结束后应回 common。"""
        self.conn.websocket = object()
        self.conn._closed = False
        self.conn.clear_queues = lambda: None
        self.conn.config = {"session_state": {"mode": "common"}}
        self.conn.tts = object()
        self.conn.client_abort = False

        spoken = []

        def _fake_speak(conn, text):
            spoken.append(text)

        import sys
        import types

        fake_intent = types.ModuleType("core.handle.intentHandler")
        fake_intent.speak_txt = _fake_speak
        prev_intent = sys.modules.get("core.handle.intentHandler")
        # abortHandle 依赖 websocket.send；用桩跳过真实 abort 模块
        fake_abort = types.ModuleType("core.handle.abortHandle")

        async def _fake_abort(conn):
            conn.client_abort = True
            if hasattr(conn, "clear_queues"):
                conn.clear_queues()
            conn.session_sm.transition(SessionEvent.ABORT, detail="test")
            # 模拟 clearSpeakStatus：若 active 已摘掉则不会还原 mode
            clear_speak_status(conn)

        fake_abort.handleAbortMessage = _fake_abort
        prev_abort = sys.modules.get("core.handle.abortHandle")
        sys.modules["core.handle.intentHandler"] = fake_intent
        sys.modules["core.handle.abortHandle"] = fake_abort
        try:
            ok1 = await speak_broadcast_text(self.conn, "广播A")
            self.assertTrue(ok1)
            self.assertEqual(self.conn._broadcast_restore_mode, DEFAULT_SESSION_MODE)
            self.assertEqual(self.conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)

            ok2 = await speak_broadcast_text(self.conn, "广播B")
            self.assertTrue(ok2)
            # 关键：仍是首次快照的 common，而非临时 play_only
            self.assertEqual(self.conn._broadcast_restore_mode, DEFAULT_SESSION_MODE)
            self.assertTrue(self.conn._broadcast_speak_active)
            self.assertEqual(spoken, ["广播A", "广播B"])
            self.assertEqual(self.conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)

            clear_speak_status(self.conn)
            self.assertFalse(getattr(self.conn, "_broadcast_speak_active", False))
            self.assertEqual(self.conn.session_sm.mode, DEFAULT_SESSION_MODE)
        finally:
            if prev_intent is None:
                sys.modules.pop("core.handle.intentHandler", None)
            else:
                sys.modules["core.handle.intentHandler"] = prev_intent
            if prev_abort is None:
                sys.modules.pop("core.handle.abortHandle", None)
            else:
                sys.modules["core.handle.abortHandle"] = prev_abort

    async def test_hot_reload_mode_during_broadcast_updates_restore(self):
        """广播中热更新 mode：只改恢复目标，结束后与配置一致。"""
        self.conn.websocket = object()
        self.conn._closed = False
        self.conn.clear_queues = lambda: None
        self.conn.config = {"session_state": {"mode": "common"}}

        ok = await apply_session_mode(
            self.conn, PLAY_ONLY_SESSION_MODE, force_interrupt=True
        )
        self.assertTrue(ok)
        self.conn._broadcast_speak_active = True
        self.conn._broadcast_restore_mode = DEFAULT_SESSION_MODE
        self.assertTrue(
            transition_session(
                self.conn, SessionEvent.TTS_START, detail="broadcast_speak"
            )
        )
        self.assertEqual(self.conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)

        # 热更新到 play_only：当前已是临时 play_only，应只改 restore
        ok = await apply_session_mode(
            self.conn, PLAY_ONLY_SESSION_MODE, force_interrupt=True
        )
        self.assertTrue(ok)
        self.assertEqual(self.conn._broadcast_restore_mode, PLAY_ONLY_SESSION_MODE)
        self.assertEqual(self.conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)
        self.assertEqual(self.conn.session_sm.state, SessionState.SPEAKING)

        clear_speak_status(self.conn)
        self.assertFalse(getattr(self.conn, "_broadcast_speak_active", False))
        self.assertEqual(self.conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)
        self.assertEqual(
            self.conn.config.get("session_state", {}).get("mode"),
            PLAY_ONLY_SESSION_MODE,
        )


if __name__ == "__main__":
    unittest.main()
