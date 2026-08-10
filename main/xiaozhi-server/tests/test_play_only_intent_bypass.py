#!/usr/bin/env python3
"""play_only：意图 await 后必须再门禁，禁止 intent_handled 早退绕过。"""

from __future__ import annotations

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.utils.session_state import (
    PLAY_ONLY_SESSION_MODE,
    SessionState,
    SessionStateMachine,
    apply_session_mode,
    transition_session,
)


def _ensure_opuslib_stub():
    if "opuslib_next" in sys.modules:
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
    """避免 `from core.handle import X` 命中包上的旧属性缓存。"""
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


class TestPlayOnlyIntentBypass(unittest.IsolatedAsyncioTestCase):
    async def test_start_to_chat_rechecks_play_only_before_intent_early_return(self):
        """intent_handled=True 时仍须被 play_only 拦住，且不得 submit chat。"""
        _ensure_opuslib_stub()

        async def _intent_then_flip(conn, _text):
            await apply_session_mode(conn, PLAY_ONLY_SESSION_MODE, force_interrupt=False)
            return True

        prev_mods = {
            name: sys.modules.get(name)
            for name in (
                "core.handle.intentHandler",
                "core.handle.sendAudioHandle",
            )
        }
        _stub_module(
            "core.handle.intentHandler",
            handle_user_intent=_intent_then_flip,
            speak_txt=lambda *_a, **_k: None,
        )
        _stub_module(
            "core.handle.sendAudioHandle",
            send_stt_message=AsyncMock(),
            SentenceType=SimpleNamespace(FIRST="FIRST", LAST="LAST", MIDDLE="MIDDLE"),
        )
        _drop_pkg_attr("core.handle.receiveAudioHandle", "receiveAudioHandle")

        try:
            from core.handle import receiveAudioHandle

            sm = SessionStateMachine(session_id="t1", mode="common")
            denied = {"n": 0}

            class _Conn:
                def __init__(self):
                    self.session_sm = sm
                    self.config = {"session_state": {"mode": "common"}}
                    self.logger = MagicMock()
                    self.logger.bind.return_value = self.logger
                    self.need_bind = False
                    self.max_output_size = 0
                    self.client_is_speaking = False
                    self.client_listen_mode = "auto"
                    self.introduced_speakers = set()
                    self.current_speaker = None
                    self.headers = {}
                    self.executor = MagicMock()
                    self.tts = MagicMock()
                    self.stop_event = None
                    self.client_abort = False
                    self.sentence_id = None

                def transition_session(self, event, detail="", force=False):
                    return transition_session(self, event, detail=detail, force=force)

            conn = _Conn()
            with patch.object(
                receiveAudioHandle,
                "speak_play_only_denied",
                side_effect=lambda c: denied.__setitem__("n", denied["n"] + 1),
            ), patch(
                "core.utils.resilience.check_system_overload", return_value=None
            ):
                ok = await receiveAudioHandle.startToChat(conn, "开灯")

            self.assertFalse(ok)
            # intent_handled=True：假定意图路径已处理降级，避免重复播报
            self.assertEqual(denied["n"], 0)
            conn.executor.submit.assert_not_called()
            self.assertEqual(conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)
        finally:
            _drop_pkg_attr("core.handle.receiveAudioHandle", "receiveAudioHandle")
            for name, old in prev_mods.items():
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old

    async def test_start_to_chat_rechecks_after_stt_await(self):
        """STT await 后若已切入广播/play_only，不得清 abort 或 submit chat。"""
        _ensure_opuslib_stub()

        async def _no_intent(_conn, _text):
            return False

        async def _stt_then_broadcast(conn, _text):
            conn._broadcast_speak_active = True
            await apply_session_mode(conn, PLAY_ONLY_SESSION_MODE, force_interrupt=False)

        prev_mods = {
            name: sys.modules.get(name)
            for name in (
                "core.handle.intentHandler",
                "core.handle.sendAudioHandle",
            )
        }
        _stub_module(
            "core.handle.intentHandler",
            handle_user_intent=_no_intent,
            speak_txt=lambda *_a, **_k: None,
        )
        _stub_module(
            "core.handle.sendAudioHandle",
            send_stt_message=_stt_then_broadcast,
            SentenceType=SimpleNamespace(FIRST="FIRST", LAST="LAST", MIDDLE="MIDDLE"),
        )
        _drop_pkg_attr("core.handle.receiveAudioHandle", "receiveAudioHandle")
        try:
            from core.handle import receiveAudioHandle

            sm = SessionStateMachine(session_id="t1b", mode="common")
            denied = {"n": 0}

            class _Conn:
                def __init__(self):
                    self.session_sm = sm
                    self.config = {"session_state": {"mode": "common"}}
                    self.logger = MagicMock()
                    self.logger.bind.return_value = self.logger
                    self.need_bind = False
                    self.max_output_size = 0
                    self.client_is_speaking = False
                    self.client_listen_mode = "auto"
                    self.introduced_speakers = set()
                    self.current_speaker = None
                    self.headers = {}
                    self.executor = MagicMock()
                    self.tts = MagicMock()
                    self.stop_event = None
                    self.client_abort = True
                    self.sentence_id = "pre"
                    self._broadcast_speak_active = False

                def transition_session(self, event, detail="", force=False):
                    return transition_session(self, event, detail=detail, force=force)

            conn = _Conn()
            with patch.object(
                receiveAudioHandle,
                "speak_play_only_denied",
                side_effect=lambda c: denied.__setitem__("n", denied["n"] + 1),
            ), patch(
                "core.utils.resilience.check_system_overload", return_value=None
            ):
                ok = await receiveAudioHandle.startToChat(conn, "你好")

            self.assertFalse(ok)
            self.assertTrue(conn.client_abort)
            self.assertEqual(conn.sentence_id, "pre")
            conn.executor.submit.assert_not_called()
            self.assertEqual(denied["n"], 1)
            self.assertEqual(conn.session_sm.state, SessionState.IDLE)
        finally:
            _drop_pkg_attr("core.handle.receiveAudioHandle", "receiveAudioHandle")
            for name, old in prev_mods.items():
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old

    async def test_handle_user_intent_blocks_after_llm_await(self):
        """LLM 意图 await 结束后若已是 play_only，不得进入 process_intent_result。"""
        _ensure_opuslib_stub()

        # 切断 helloHandle / plugins 重依赖
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
                {"enqueue_tool_report": lambda *_a, **_k: None},
            ),
            (
                "core.utils.util",
                {"remove_punctuation_and_length": lambda t: (len(t), t)},
            ),
            (
                "core.providers.tts.dto.dto",
                {
                    "ContentType": SimpleNamespace(ACTION="ACTION", TEXT="TEXT"),
                    "TTSMessageDTO": object,
                    "SentenceType": SimpleNamespace(FIRST="FIRST", LAST="LAST"),
                },
            ),
            (
                "core.utils.dialogue",
                {"Message": object},
            ),
        ):
            prev[name] = sys.modules.get(name)
            _stub_module(name, **attrs)

        sys.modules.pop("core.handle.intentHandler", None)
        try:
            from core.handle import intentHandler

            sm = SessionStateMachine(session_id="t2", mode="common")
            processed = {"n": 0}
            denied = {"n": 0}
            conn = SimpleNamespace(
                session_sm=sm,
                config={"session_state": {"mode": "common"}},
                logger=MagicMock(),
                intent_type="intent_llm",
                cmd_exit=[],
                current_speaker=None,
                sentence_id=None,
                tts=MagicMock(),
                stop_event=None,
                client_abort=False,
            )
            conn.logger.bind.return_value = conn.logger

            async def _llm(_conn, _text):
                await apply_session_mode(
                    conn, PLAY_ONLY_SESSION_MODE, force_interrupt=False
                )
                return '{"function_call":{"name":"get_weather","arguments":{}}}'

            with patch.object(
                intentHandler, "analyze_intent_with_llm", side_effect=_llm
            ), patch.object(
                intentHandler,
                "process_intent_result",
                side_effect=lambda *a, **k: processed.__setitem__(
                    "n", processed["n"] + 1
                )
                or True,
            ), patch.object(
                intentHandler,
                "speak_play_only_denied",
                side_effect=lambda c: denied.__setitem__("n", denied["n"] + 1),
            ):
                handled = await intentHandler.handle_user_intent(conn, "今天天气")

            self.assertTrue(handled)
            self.assertEqual(processed["n"], 0)
            self.assertEqual(denied["n"], 1)
            self.assertEqual(conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)
            self.assertEqual(conn.session_sm.state, SessionState.IDLE)
        finally:
            sys.modules.pop("core.handle.intentHandler", None)
            for name, old in prev.items():
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old


if __name__ == "__main__":
    unittest.main()
