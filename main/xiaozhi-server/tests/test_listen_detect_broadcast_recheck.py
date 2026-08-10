#!/usr/bin/env python3
"""listen detect：device_call / wakeup_no_greeting 在 await 后须复检广播。"""

from __future__ import annotations

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core.utils.session_state import (
    PLAY_ONLY_SESSION_MODE,
    SessionStateMachine,
    apply_session_mode,
)


def _stub(name: str, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


def _drop(modname: str, attr: str):
    parent, _, _ = modname.rpartition(".")
    pkg = sys.modules.get(parent)
    if pkg is not None and hasattr(pkg, attr):
        try:
            delattr(pkg, attr)
        except Exception:
            pass
    sys.modules.pop(modname, None)


class TestListenDetectBroadcastRecheck(unittest.IsolatedAsyncioTestCase):
    async def test_device_call_aborts_if_broadcast_during_await(self):
        denied = {"n": 0}
        queued = {"n": 0}

        async def _stt_flip(conn, _text):
            await apply_session_mode(conn, PLAY_ONLY_SESSION_MODE, force_interrupt=False)
            conn._broadcast_speak_active = True
            conn._broadcast_sentence_id = "bcast"

        class _TTS:
            def store_tts_text(self, *_a, **_k):
                queued["n"] += 1

            def tts_one_sentence(self, *_a, **_k):
                queued["n"] += 1

            @property
            def tts_text_queue(self):
                return SimpleNamespace(put=lambda *_a, **_k: queued.__setitem__("n", queued["n"] + 1))

        prev = {}
        for name, attrs in (
            (
                "core.handle.receiveAudioHandle",
                {"startToChat": AsyncMock()},
            ),
            (
                "core.handle.reportHandle",
                {"enqueue_asr_report": lambda *_a, **_k: None},
            ),
            (
                "core.handle.sendAudioHandle",
                {
                    "send_stt_message": _stt_flip,
                    "send_tts_message": AsyncMock(),
                },
            ),
            (
                "core.utils.util",
                {"remove_punctuation_and_length": lambda t: (len(t), t)},
            ),
            (
                "core.utils.dialogue",
                {"Message": object},
            ),
            (
                "core.providers.asr.dto.dto",
                {"InterfaceType": SimpleNamespace(STREAM="STREAM")},
            ),
            (
                "core.providers.tts.dto.dto",
                {
                    "ContentType": SimpleNamespace(ACTION="ACTION", TEXT="TEXT"),
                    "TTSMessageDTO": object,
                    "SentenceType": SimpleNamespace(
                        FIRST="FIRST", LAST="LAST", MIDDLE="MIDDLE"
                    ),
                },
            ),
        ):
            prev[name] = sys.modules.get(name)
            _stub(name, **attrs)

        _drop("core.handle.textHandler.listenMessageHandler", "listenMessageHandler")
        # package path may be core.handle.textHandler.listenMessageHandler
        sys.modules.pop("core.handle.textHandler.listenMessageHandler", None)

        try:
            from core.handle.textHandler.listenMessageHandler import (
                ListenTextMessageHandler,
            )
            from core.utils.session_state import speak_play_only_denied as real_deny

            sm = SessionStateMachine(session_id="d1", mode="common")
            conn = SimpleNamespace(
                session_sm=sm,
                config={
                    "session_state": {"mode": "common"},
                    "wakeup_words": ["小智"],
                    "enable_greeting": True,
                },
                logger=MagicMock(),
                client_have_voice=False,
                client_listen_mode="auto",
                last_activity_time=0,
                incoming_call=False,
                sentence_id=None,
                tts=_TTS(),
                dialogue=SimpleNamespace(put=lambda *_a, **_k: None),
                _broadcast_speak_active=False,
                _broadcast_soft_barge_in=False,
            )
            conn.logger.bind.return_value = conn.logger
            conn.reset_audio_states = lambda: None
            conn.enter_detect = lambda **k: None

            with patch(
                "core.handle.textHandler.listenMessageHandler.speak_play_only_denied",
                side_effect=lambda c: denied.__setitem__("n", denied["n"] + 1),
            ):
                await ListenTextMessageHandler().handle(
                    conn, {"state": "detect", "text": "[device_call]有人找你"}
                )

            self.assertEqual(queued["n"], 0)
            self.assertEqual(denied["n"], 1)
            self.assertEqual(conn.session_sm.mode, PLAY_ONLY_SESSION_MODE)
        finally:
            sys.modules.pop("core.handle.textHandler.listenMessageHandler", None)
            for name, old in prev.items():
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old


if __name__ == "__main__":
    unittest.main()
