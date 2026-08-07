#!/usr/bin/env python3
"""会话状态机单测：合法转移 / 非法拒绝 / 结构化日志 + 连接接线。"""

from __future__ import annotations

import asyncio
import unittest
from typing import List

from core.utils.session_state import (
    LEGAL_TRANSITIONS,
    SessionEvent,
    SessionState,
    SessionStateMachine,
    clear_speak_status,
    enter_detect,
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
        self.assertIn("session=s1 event=listen_start from=IDLE to=LISTENING", joined)
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


if __name__ == "__main__":
    unittest.main()
