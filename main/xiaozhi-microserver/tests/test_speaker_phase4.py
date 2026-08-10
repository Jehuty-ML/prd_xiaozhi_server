"""Phase-4 unit tests: EchoTTS, SpeakSession, rate controller, abort."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

SPEAKER_ROOT = Path(__file__).resolve().parents[1] / "xiaozhi-audio-speaker"
MICRO_ROOT = Path(__file__).resolve().parents[1]


def _use_speaker_app() -> None:
    """Agent and speaker both ship package `app` — pin speaker for this file."""
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]
    paths = [
        str(SPEAKER_ROOT),
        str(MICRO_ROOT / "common"),
        str(MICRO_ROOT / "common" / "generated"),
    ]
    for s in paths:
        if s in sys.path:
            sys.path.remove(s)
    for s in reversed(paths):
        sys.path.insert(0, s)


_use_speaker_app()


@pytest.fixture(autouse=True)
def _speaker_path():
    _use_speaker_app()
    yield


class _FakeDownlink:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def send(self, **kwargs):  # noqa: ANN003
        self.events.append(dict(kwargs))
        return True

    def send_state(self, **kwargs):  # noqa: ANN003
        kwargs.setdefault("audio", b"")
        kwargs.setdefault("format", "")
        self.events.append(dict(kwargs))
        return True


def test_echo_tts_frames():
    from app.providers.tts import create_tts

    tts = create_tts({"selected_module": {"TTS": "EchoTTS"}, "TTS": {"EchoTTS": {"type": "echo"}}})
    frames = tts.synthesize_opus_frames("你好")
    assert isinstance(frames, list)
    assert len(frames) >= 1
    assert all(isinstance(f, (bytes, bytearray)) for f in frames)


def test_rate_controller_pacing():
    from app.core.rate_controller import SyncAudioRateController

    ctrl = SyncAudioRateController(frame_duration_ms=20)
    sent: list[bytes] = []
    for i in range(3):
        ctrl.add_audio(bytes([i]))
    t0 = time.monotonic()
    n = ctrl.drain(lambda f: sent.append(f))
    elapsed = time.monotonic() - t0
    assert n == 3
    assert sent == [b"\x00", b"\x01", b"\x02"]
    # 3 frames @ 20ms → ~40ms wait after first immediate send
    assert elapsed >= 0.03


def test_speak_session_start_sentence_stop():
    from app.core.speak_session import SpeakJob, SpeakSession
    from app.providers.tts.echo import EchoTTS

    dl = _FakeDownlink()
    sess = SpeakSession("c1", EchoTTS({}), dl, frame_duration_ms=1)
    mid = "msg1"
    sess.enqueue(SpeakJob(message_id=mid, text="第一句。", index=1, total=0, end=False))
    sess.enqueue(SpeakJob(message_id=mid, text="", index=1, total=1, end=True))
    # Wait for worker
    deadline = time.time() + 5
    while time.time() < deadline:
        states = [e.get("state") for e in dl.events if e.get("state")]
        if "start" in states and "sentence_start" in states and "stop" in states:
            break
        time.sleep(0.05)
    states = [e.get("state") for e in dl.events if e.get("state")]
    assert "start" in states
    assert "sentence_start" in states
    assert "stop" in states
    assert any(e.get("audio") for e in dl.events)


def test_speak_session_abort_clears():
    from app.core.speak_session import SpeakJob, SpeakSession
    from app.providers.tts.echo import EchoTTS

    dl = _FakeDownlink()
    sess = SpeakSession("c2", EchoTTS({}), dl, frame_duration_ms=50)
    mid = "msg2"
    # Enqueue many slow frames then abort quickly
    for i in range(5):
        sess.enqueue(
            SpeakJob(message_id=mid, text=f"句{i}", index=i + 1, total=0, end=False)
        )
    time.sleep(0.05)
    sess.abort("unit")
    time.sleep(0.2)
    stop_events = [e for e in dl.events if e.get("state") == "stop"]
    assert stop_events


def test_broadcast_lease_rejects_foreign_tts():
    from app.core.speak_session import SpeakJob, SpeakSession
    from app.providers.tts.echo import EchoTTS

    dl = _FakeDownlink()
    sess = SpeakSession("c3", EchoTTS({}), dl, frame_duration_ms=1)
    bcast = "bcast-1"
    assert sess.enqueue(
        SpeakJob(
            message_id=bcast,
            text="广播内容",
            index=1,
            total=1,
            end=False,
            is_broadcast=True,
        )
    )
    assert sess.broadcast_active()
    # Dialogue TTS with a different message_id must not steal the line.
    assert not sess.enqueue(
        SpeakJob(message_id="chat-1", text="用户对话", index=1, total=0, end=False)
    )
    assert sess.enqueue(
        SpeakJob(
            message_id=bcast,
            text="",
            index=1,
            total=1,
            end=True,
            is_broadcast=True,
        )
    )
    deadline = time.time() + 5
    while time.time() < deadline and sess.broadcast_active():
        time.sleep(0.05)
    assert not sess.broadcast_active()
    # After broadcast ends, normal dialogue TTS is allowed again.
    assert sess.enqueue(
        SpeakJob(message_id="chat-2", text="恢复对话", index=1, total=0, end=False)
    )


def test_abort_force_clears_broadcast_lease():
    from app.core.speak_session import SpeakJob, SpeakSession
    from app.providers.tts.echo import EchoTTS

    dl = _FakeDownlink()
    sess = SpeakSession("c4", EchoTTS({}), dl, frame_duration_ms=50)
    assert sess.enqueue(
        SpeakJob(
            message_id="bcast-2",
            text="卡住广播",
            index=1,
            total=1,
            end=False,
            is_broadcast=True,
        )
    )
    assert sess.broadcast_active()
    sess.abort("force_end_broadcast")
    assert not sess.broadcast_active()
