"""Phase-5 unit tests: StubVAD, StubASR, listen session, AEC no-op."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PREPROCESS_ROOT = Path(__file__).resolve().parents[1] / "xiaozhi-audio-preprocess"
RECEIVER_ROOT = Path(__file__).resolve().parents[1] / "xiaozhi-audio-receiver"
MICRO_ROOT = Path(__file__).resolve().parents[1]


def _clear_app() -> None:
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]


def _use_preprocess_app() -> None:
    _clear_app()
    paths = [
        str(PREPROCESS_ROOT),
        str(MICRO_ROOT / "common"),
        str(MICRO_ROOT / "common" / "generated"),
    ]
    for s in list(sys.path):
        if s in paths or s.endswith("xiaozhi-audio-receiver") or s.endswith(
            "xiaozhi-audio-speaker"
        ):
            try:
                sys.path.remove(s)
            except ValueError:
                pass
    for s in reversed(paths):
        sys.path.insert(0, s)


def _use_receiver_app() -> None:
    _clear_app()
    paths = [
        str(RECEIVER_ROOT),
        str(MICRO_ROOT / "common"),
        str(MICRO_ROOT / "common" / "generated"),
    ]
    for s in list(sys.path):
        if "xiaozhi-audio-" in s.replace("\\", "/"):
            try:
                sys.path.remove(s)
            except ValueError:
                pass
    for s in reversed(paths):
        sys.path.insert(0, s)


def _tone_pcm(samples: int = 512, amplitude: int = 8000) -> bytes:
    """Simple square-ish int16 tone for energy VAD."""
    out = bytearray()
    for i in range(samples):
        v = amplitude if (i // 40) % 2 == 0 else -amplitude
        out += int(v).to_bytes(2, "little", signed=True)
    return bytes(out)


def _silence_pcm(samples: int = 512) -> bytes:
    return b"\x00" * (samples * 2)


def test_stub_asr_min_bytes():
    _use_receiver_app()
    from app.providers.asr import create_asr

    asr = create_asr(
        {
            "selected_module": {"ASR": "StubASR"},
            "ASR": {"StubASR": {"type": "stub", "fixed_text": "测试", "min_pcm_bytes": 100}},
        }
    )
    assert asr.recognize(b"\x00" * 50) == ""
    assert asr.recognize(b"\x00" * 200) == "测试"


def test_stub_vad_voice_then_silence_stop():
    _use_preprocess_app()
    from app.core.listen_session import ListenSession
    from app.providers.vad.stub import StubVAD

    vad = StubVAD(
        {
            "energy_threshold": 200,
            "min_silence_duration_ms": 50,
            "frame_window_threshold": 2,
        }
    )
    sess = ListenSession(client_id="c1", listen_mode="auto")

    # Feed several voiced chunks (512 samples each)
    have = False
    for _ in range(6):
        have = vad.is_vad(sess, _tone_pcm())
    assert sess.client_have_voice is True
    assert have is True

    # Silence long enough to trip stop (window must flip + silence duration)
    import time

    for _ in range(10):
        vad.is_vad(sess, _silence_pcm())
        time.sleep(0.02)
    assert sess.client_voice_stop is True


def test_listen_session_manual_flush():
    _use_preprocess_app()
    from app.core.listen_session import ListenSessionStore
    from app.providers.vad.stub import StubVAD

    recognized: list[bytes] = []
    forwarded: list[str] = []

    store = ListenSessionStore()
    store.configure(
        StubVAD({"energy_threshold": 99999}),  # never auto-voice
        min_asr_pcm_bytes=100,
        asr_fn=lambda cid, mid, pcm: (recognized.append(pcm) or "你好"),
        forward_text_fn=lambda cid, mid, text: (forwarded.append(text) or "ok"),
        notify_fn=lambda *a: None,
        send_device_fn=lambda *a: None,
        abort_peers_fn=lambda *a: None,
    )

    store.control_listen("c2", state="start", mode="manual")
    # Manual: buffer PCM frames (format=pcm)
    pcm = _tone_pcm(1000)
    store.ingest_audio("c2", pcm, format="pcm", message_id="m1")
    store.ingest_audio("c2", pcm, format="pcm", message_id="m2")
    result = store.control_listen("c2", state="stop", mode="manual", message_id="m3")
    assert result == "ok"
    assert forwarded == ["你好"]
    assert recognized and len(recognized[0]) >= 100


def test_listen_session_abort_clears():
    _use_preprocess_app()
    from app.core.listen_session import ListenSessionStore
    from app.providers.vad.stub import StubVAD

    store = ListenSessionStore()
    store.configure(
        StubVAD({}),
        asr_fn=lambda *a: "",
        forward_text_fn=lambda *a: "",
        notify_fn=lambda *a: None,
        send_device_fn=lambda *a: None,
        abort_peers_fn=lambda *a: None,
    )
    store.control_listen("c3", state="start", mode="auto")
    store.ingest_audio("c3", _tone_pcm(), format="pcm")
    ok = store.abort("c3", "unit")
    assert ok is True
    sess = store.get_or_create("c3")
    assert sess.asr_audio == []
    assert sess.client_have_voice is False


def test_aec_noop_without_cache():
    _use_preprocess_app()
    from app.core.aec import AecCache

    cache = AecCache()
    pcm = _tone_pcm(960)
    out = cache.apply(pcm, timestamp=1)
    assert out == pcm


def test_aec_push_and_apply_returns_bytes():
    _use_preprocess_app()
    from app.core.aec import AecCache

    cache = AecCache()
    ref = _tone_pcm(960, amplitude=5000)
    cache.push(1000, ref)
    cache.push(1060, ref)
    mic = _tone_pcm(960, amplitude=6000)
    out = cache.apply(mic, timestamp=1030)
    assert isinstance(out, (bytes, bytearray))
    assert len(out) == len(mic)
