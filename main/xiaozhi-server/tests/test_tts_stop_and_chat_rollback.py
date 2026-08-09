#!/usr/bin/env python3
"""TTS stop 取消清态 / CHAT_START 发送失败回滚 / 音频发送循环唤醒排空等待。"""

from __future__ import annotations

import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from core.utils.session_state import (
    DEFAULT_SESSION_MODE,
    PLAY_ONLY_SESSION_MODE,
    SessionEvent,
    SessionState,
    SessionStateMachine,
    clear_speak_status,
    transition_session,
)


def _ensure_opuslib_stub():
    """本机可能无 Opus 动态库；单测只验证状态机/清理路径。"""
    if "opuslib_next" in sys.modules:
        return
    stub = types.ModuleType("opuslib_next")
    stub.Encoder = object
    stub.Decoder = object
    sys.modules["opuslib_next"] = stub
    api = types.ModuleType("opuslib_next.api")
    sys.modules["opuslib_next.api"] = api


class TestAudioRateControllerUnblocksWaiters(unittest.IsolatedAsyncioTestCase):
    async def test_send_loop_exception_sets_queue_empty(self):
        sys.modules.pop("core.utils.audioRateController", None)
        with patch("config.logger.setup_logging", return_value=MagicMock()):
            from core.utils.audioRateController import AudioRateController

        rc = AudioRateController(60)
        rc.add_audio(b"frame")

        async def _boom(_packet):
            raise RuntimeError("websocket send failed")

        task = rc.start_sending(_boom)
        await asyncio.wait_for(rc.queue_empty_event.wait(), timeout=1.0)
        self.assertTrue(rc.queue_empty_event.is_set())
        self.assertEqual(len(rc.queue), 0)
        await asyncio.sleep(0)  # let task finish
        self.assertTrue(task.done())


class TestSendTtsStopClearsOnCancel(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_wait_still_clears_broadcast_play_only(self):
        """TTS stop 等待被取消时仍须 clearSpeakStatus，避免永久 play_only。"""
        _ensure_opuslib_stub()
        # util/sendAudioHandle 可能已因 opus 失败未加载；清掉后重导
        for name in (
            "core.handle.sendAudioHandle",
            "core.utils.util",
        ):
            sys.modules.pop(name, None)

        from core.handle import sendAudioHandle

        sm = SessionStateMachine(session_id="t1", mode=PLAY_ONLY_SESSION_MODE)
        cleared = {"n": 0}

        class _Conn:
            def __init__(self):
                self.session_id = "t1"
                self.sentence_id = "s1"
                self.config = {"session_state": {"mode": PLAY_ONLY_SESSION_MODE}}
                self.session_sm = sm
                self.client_is_speaking = True
                self._broadcast_speak_active = True
                self._broadcast_restore_mode = DEFAULT_SESSION_MODE
                self.audio_rate_controller = None

                async def _send(_payload):
                    return None

                self.websocket = SimpleNamespace(send=_send)

            def clearSpeakStatus(self):
                cleared["n"] += 1
                clear_speak_status(self)

        conn = _Conn()
        self.assertTrue(
            transition_session(conn, SessionEvent.TTS_START, detail="broadcast")
        )
        self.assertEqual(conn.session_sm.state, SessionState.SPEAKING)

        async def _hang(_conn):
            await asyncio.sleep(3600)

        with patch.object(sendAudioHandle, "_wait_for_audio_completion", _hang):
            task = asyncio.create_task(
                sendAudioHandle.send_tts_message(conn, "stop", None)
            )
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertEqual(cleared["n"], 1)
        self.assertFalse(getattr(conn, "_broadcast_speak_active", False))
        self.assertEqual(conn.session_sm.mode, DEFAULT_SESSION_MODE)
        self.assertEqual(conn.session_sm.state, SessionState.IDLE)


class TestStartToChatSttFailureRollback(unittest.IsolatedAsyncioTestCase):
    async def test_send_stt_failure_resets_thinking(self):
        _ensure_opuslib_stub()
        # 切断 intent/hello 重依赖链
        fake_intent = types.ModuleType("core.handle.intentHandler")

        async def _no_intent(_conn, _text):
            return False

        fake_intent.handle_user_intent = _no_intent
        fake_intent.speak_txt = lambda *_a, **_k: None
        prev_intent = sys.modules.get("core.handle.intentHandler")
        sys.modules["core.handle.intentHandler"] = fake_intent

        for name in (
            "core.handle.receiveAudioHandle",
            "core.handle.sendAudioHandle",
            "core.utils.util",
        ):
            sys.modules.pop(name, None)

        try:
            from core.handle import receiveAudioHandle

            sm = SessionStateMachine(session_id="c1")
            submitted = []

            class _Conn:
                def __init__(self):
                    self.session_sm = sm
                    self.config = {}
                    self.need_bind = False
                    self.max_output_size = 0
                    self.client_is_speaking = False
                    self.client_listen_mode = "auto"
                    self.client_abort = False
                    self.introduced_speakers = set()
                    self.current_speaker = None
                    self.headers = {}
                    self.logger = MagicMock()
                    self.logger.bind.return_value = self.logger
                    self.executor = SimpleNamespace(
                        submit=lambda fn, *a, **k: submitted.append((fn, a))
                    )
                    self.tts = object()

                def transition_session(self, event, detail="", force=False):
                    return transition_session(self, event, detail=detail, force=force)

            conn = _Conn()

            async def _fail_stt(_conn, _text):
                raise ConnectionError("ws send failed")

            with patch.object(
                receiveAudioHandle, "is_play_only_mode", return_value=False
            ), patch(
                "core.utils.resilience.check_system_overload", return_value=None
            ), patch.object(
                receiveAudioHandle, "send_stt_message", _fail_stt
            ):
                with self.assertRaises(ConnectionError):
                    await receiveAudioHandle.startToChat(conn, "你好")

            self.assertEqual(conn.session_sm.state, SessionState.IDLE)
            self.assertEqual(submitted, [])
        finally:
            if prev_intent is None:
                sys.modules.pop("core.handle.intentHandler", None)
            else:
                sys.modules["core.handle.intentHandler"] = prev_intent


if __name__ == "__main__":
    unittest.main()
