#!/usr/bin/env python3
"""广播/play_only：chat 与 speak_txt 不得在 await 后抢写/注入 sentence_id。"""

from __future__ import annotations

import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.utils.session_state import (
    PLAY_ONLY_SESSION_MODE,
    SessionEvent,
    SessionState,
    SessionStateMachine,
    should_block_user_dialogue_tts,
    transition_session,
)


def _ensure_opuslib_stub():
    if "opuslib_next" in sys.modules and hasattr(
        sys.modules["opuslib_next"], "Encoder"
    ):
        return
    stub = types.ModuleType("opuslib_next")
    stub.Encoder = object
    stub.Decoder = object
    sys.modules["opuslib_next"] = stub
    sys.modules["opuslib_next.api"] = types.ModuleType("opuslib_next.api")


def _stub_module(name: str, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _drop_pkg_attr(modname: str, attr: str):
    parent, _, child = modname.rpartition(".")
    if not parent:
        return
    pkg = sys.modules.get(parent)
    if pkg is not None and hasattr(pkg, attr):
        try:
            delattr(pkg, attr)
        except Exception:
            pass
    sys.modules.pop(modname, None)


class TestChatBroadcastSentenceGuard(unittest.TestCase):
    def test_predicate_blocks_when_broadcast_active(self):
        """广播 active / soft_barge / 临时 play_only 均应挡住用户对话 TTS。"""
        sm = SessionStateMachine(session_id="p1", mode=PLAY_ONLY_SESSION_MODE)
        conn = SimpleNamespace(
            session_sm=sm,
            _broadcast_speak_active=True,
            _broadcast_soft_barge_in=False,
        )
        self.assertTrue(should_block_user_dialogue_tts(conn))

        conn._broadcast_speak_active = False
        conn._broadcast_soft_barge_in = True
        self.assertTrue(should_block_user_dialogue_tts(conn))

        conn._broadcast_soft_barge_in = False
        self.assertTrue(should_block_user_dialogue_tts(conn))  # play_only mode

        sm2 = SessionStateMachine(session_id="p2", mode="common")
        conn2 = SimpleNamespace(
            session_sm=sm2,
            _broadcast_speak_active=False,
            _broadcast_soft_barge_in=False,
        )
        self.assertFalse(should_block_user_dialogue_tts(conn2))

    def test_chat_body_guard_must_run_before_sentence_id_assign(self):
        """模拟 _chat_body depth==0 入口：门禁命中时不得改写广播 sid。"""
        sm = SessionStateMachine(session_id="chat-bcast", mode=PLAY_ONLY_SESSION_MODE)
        conn = SimpleNamespace(
            session_sm=sm,
            config={"session_state": {"mode": PLAY_ONLY_SESSION_MODE}},
            client_is_speaking=False,
            sentence_id="broadcast-sid",
            _broadcast_speak_active=True,
            _broadcast_soft_barge_in=False,
        )
        self.assertTrue(
            transition_session(conn, SessionEvent.TTS_START, detail="broadcast")
        )
        self.assertEqual(conn.session_sm.state, SessionState.SPEAKING)
        broadcast_sid = conn.sentence_id
        assigned = []

        # 与 connection._chat_body 入口同一顺序：先门禁，再赋值
        if should_block_user_dialogue_tts(conn):
            result = None
        else:
            conn.sentence_id = "chat-stolen"
            assigned.append(conn.sentence_id)
            result = True

        self.assertIsNone(result)
        self.assertEqual(conn.sentence_id, broadcast_sid)
        self.assertEqual(assigned, [])
        self.assertEqual(conn.session_sm.state, SessionState.SPEAKING)

    def test_speak_txt_noops_during_broadcast_unless_allowed(self):
        """意图路径 speak_txt 在广播中必须 no-op；广播自身可显式放行。"""
        _ensure_opuslib_stub()

        prev = {}
        for name, attrs in (
            (
                "core.handle.helloHandle",
                {"checkWakeupWords": AsyncMock(return_value=False)},
            ),
            (
                "plugins_func.register",
                {
                    "Action": SimpleNamespace(
                        RESPONSE="RESPONSE",
                        REQLLM="REQLLM",
                        NOTFOUND="NOTFOUND",
                        ERROR="ERROR",
                    ),
                    "ActionResponse": object,
                },
            ),
            (
                "core.handle.sendAudioHandle",
                {"send_stt_message": AsyncMock()},
            ),
            (
                "core.handle.reportHandle",
                {"enqueue_tool_report": MagicMock()},
            ),
            (
                "core.utils.util",
                {"remove_punctuation_and_length": lambda t: (0, t)},
            ),
            (
                "core.providers.tts.dto.dto",
                {
                    "ContentType": SimpleNamespace(ACTION="ACTION", TEXT="TEXT"),
                    "TTSMessageDTO": lambda **kw: SimpleNamespace(**kw),
                    "SentenceType": SimpleNamespace(FIRST="FIRST", LAST="LAST"),
                },
            ),
            (
                "core.utils.dialogue",
                {"Message": lambda **kw: SimpleNamespace(**kw)},
            ),
        ):
            prev[name] = sys.modules.get(name)
            _stub_module(name, **attrs)

        _drop_pkg_attr("core.handle.intentHandler", "intentHandler")
        try:
            from core.handle import intentHandler

            tts = MagicMock()
            tts.tts_text_queue = MagicMock()
            dialogue = MagicMock()
            conn = SimpleNamespace(
                sentence_id="bcast",
                _broadcast_speak_active=True,
                _broadcast_soft_barge_in=False,
                tts=tts,
                dialogue=dialogue,
                logger=MagicMock(),
            )
            conn.logger.bind.return_value = conn.logger

            intentHandler.speak_txt(conn, "天气晴")
            tts.tts_text_queue.put.assert_not_called()
            tts.tts_one_sentence.assert_not_called()
            dialogue.put.assert_not_called()

            intentHandler.speak_txt(
                conn, "紧急通知", allow_during_broadcast=True
            )
            self.assertGreaterEqual(tts.tts_text_queue.put.call_count, 2)
            tts.tts_one_sentence.assert_called()
            dialogue.put.assert_called()
        finally:
            _drop_pkg_attr("core.handle.intentHandler", "intentHandler")
            for name, old in prev.items():
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old


class TestIntentToolBroadcastRecheck(unittest.IsolatedAsyncioTestCase):
    async def test_intent_tool_speak_rechecks_after_tool_await(self):
        """工具 await 结束后若已切入广播，不得 speak_txt 注入广播句。"""
        _ensure_opuslib_stub()

        Action = SimpleNamespace(
            RESPONSE="RESPONSE",
            REQLLM="REQLLM",
            NOTFOUND="NOTFOUND",
            ERROR="ERROR",
        )

        class ActionResponse:
            def __init__(self, action, result=None, response=None):
                self.action = action
                self.result = result
                self.response = response

        prev = {}
        for name, attrs in (
            (
                "core.handle.helloHandle",
                {"checkWakeupWords": AsyncMock(return_value=False)},
            ),
            (
                "plugins_func.register",
                {"Action": Action, "ActionResponse": ActionResponse},
            ),
            (
                "core.handle.sendAudioHandle",
                {"send_stt_message": AsyncMock()},
            ),
            (
                "core.handle.reportHandle",
                {"enqueue_tool_report": MagicMock()},
            ),
            (
                "core.utils.util",
                {"remove_punctuation_and_length": lambda t: (0, t)},
            ),
            (
                "core.providers.tts.dto.dto",
                {
                    "ContentType": SimpleNamespace(ACTION="ACTION", TEXT="TEXT"),
                    "TTSMessageDTO": lambda **kw: SimpleNamespace(**kw),
                    "SentenceType": SimpleNamespace(FIRST="FIRST", LAST="LAST"),
                },
            ),
            (
                "core.utils.dialogue",
                {"Message": lambda **kw: SimpleNamespace(**kw)},
            ),
        ):
            prev[name] = sys.modules.get(name)
            _stub_module(name, **attrs)

        _drop_pkg_attr("core.handle.intentHandler", "intentHandler")
        try:
            from core.handle import intentHandler

            spoken = []
            denied = {"n": 0}

            class _Future:
                def result(self, timeout=None):
                    conn._broadcast_speak_active = True
                    return ActionResponse(
                        action=Action.RESPONSE,
                        result="ok",
                        response="今天晴天",
                    )

            class _Executor:
                def submit(self, fn):
                    fn()
                    return None

            sm = SessionStateMachine(session_id="intent-bcast", mode="common")
            conn = SimpleNamespace(
                session_sm=sm,
                config={"tool_call_timeout": 5},
                logger=MagicMock(),
                dialogue=MagicMock(),
                tts=MagicMock(),
                stop_event=None,
                client_abort=False,
                sentence_id="user-sid",
                _broadcast_speak_active=False,
                _broadcast_soft_barge_in=False,
                loop=object(),
                executor=_Executor(),
                func_handler=SimpleNamespace(
                    handle_llm_function_call=MagicMock()
                ),
            )
            conn.logger.bind.return_value = conn.logger

            async def _stt(_conn, _text):
                return None

            with patch(
                "core.handle.intentHandler.asyncio.run_coroutine_threadsafe",
                return_value=_Future(),
            ), patch.object(
                intentHandler,
                "speak_txt",
                side_effect=lambda *a, **k: spoken.append(
                    a[1] if len(a) > 1 else k.get("text")
                ),
            ), patch.object(
                intentHandler,
                "speak_play_only_denied",
                side_effect=lambda c: denied.__setitem__("n", denied["n"] + 1),
            ), patch.object(
                intentHandler,
                "send_stt_message",
                _stt,
            ):
                intent_data = {
                    "function_call": {
                        "name": "get_weather",
                        "arguments": {},
                    }
                }
                handled = await intentHandler.process_intent_result(
                    conn, json.dumps(intent_data), "今天天气"
                )

            self.assertTrue(handled)
            self.assertEqual(spoken, [])
            self.assertEqual(denied["n"], 1)
            self.assertTrue(conn._broadcast_speak_active)
        finally:
            _drop_pkg_attr("core.handle.intentHandler", "intentHandler")
            for name, old in prev.items():
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old


if __name__ == "__main__":
    unittest.main()
