"""Per-client listen / VAD / ASR flush session."""

from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger

from app.core.aec import AecCache
from app.core.audio_codec import OpusDecoderSession, decode_chunk
from app.providers.vad.base import VADProviderBase


@dataclass
class ListenSession:
    client_id: str
    listen_mode: str = "auto"
    aec_enabled: bool = False
    listening: bool = False
    speaking: bool = False
    client_audio_buffer: bytearray = field(default_factory=bytearray)
    client_voice_window: Any = field(default_factory=lambda: deque(maxlen=5))
    client_have_voice: bool = False
    client_voice_stop: bool = False
    last_is_voice: bool = False
    vad_last_voice_time: float = 0.0
    asr_audio: list[bytes] = field(default_factory=list)
    aec: AecCache = field(default_factory=AecCache)
    decoder: OpusDecoderSession = field(default_factory=OpusDecoderSession)
    _vad_state: Any = None
    _vad_context: Any = None

    def reset_audio_states(self) -> None:
        self.client_audio_buffer = bytearray()
        self.client_voice_window = deque(maxlen=5)
        self.client_have_voice = False
        self.client_voice_stop = False
        self.last_is_voice = False
        self.vad_last_voice_time = 0.0
        self.asr_audio = []
        self._vad_state = None
        self._vad_context = None


AsrFn = Callable[[str, str, bytes], str]
ForwardTextFn = Callable[[str, str, str], str]
NotifyFn = Callable[[str, str, str], None]
SendDeviceFn = Callable[[str, str, str], None]
AbortPeersFn = Callable[[str, str, str], None]


class ListenSessionStore:
    def __init__(
        self,
        vad: VADProviderBase | None = None,
        *,
        sample_rate: int = 16000,
        min_asr_pcm_bytes: int = 19200,
        pre_roll_frames: int = 10,
    ) -> None:
        self.vad = vad  # set in configure()
        self.sample_rate = sample_rate
        self.min_asr_pcm_bytes = min_asr_pcm_bytes
        self.pre_roll_frames = pre_roll_frames
        self._sessions: dict[str, ListenSession] = {}
        self._lock = threading.Lock()
        self.asr_fn: Optional[AsrFn] = None
        self.forward_text_fn: Optional[ForwardTextFn] = None
        self.notify_fn: Optional[NotifyFn] = None
        self.send_device_fn: Optional[SendDeviceFn] = None
        self.abort_peers_fn: Optional[AbortPeersFn] = None

    def configure(
        self,
        vad: VADProviderBase,
        *,
        sample_rate: int = 16000,
        min_asr_pcm_bytes: int = 19200,
        pre_roll_frames: int = 10,
        asr_fn: AsrFn,
        forward_text_fn: ForwardTextFn,
        notify_fn: NotifyFn,
        send_device_fn: SendDeviceFn,
        abort_peers_fn: AbortPeersFn,
    ) -> None:
        self.vad = vad
        self.sample_rate = sample_rate
        self.min_asr_pcm_bytes = min_asr_pcm_bytes
        self.pre_roll_frames = pre_roll_frames
        self.asr_fn = asr_fn
        self.forward_text_fn = forward_text_fn
        self.notify_fn = notify_fn
        self.send_device_fn = send_device_fn
        self.abort_peers_fn = abort_peers_fn

    def get_or_create(self, client_id: str) -> ListenSession:
        with self._lock:
            sess = self._sessions.get(client_id)
            if sess is None:
                sess = ListenSession(client_id=client_id)
                self._sessions[client_id] = sess
            return sess

    def abort(self, client_id: str, reason: str = "abort") -> bool:
        with self._lock:
            sess = self._sessions.get(client_id)
        if not sess:
            return False
        if self.vad:
            self.vad.release(sess)
        sess.reset_audio_states()
        sess.aec.clear()
        sess.listening = False
        sess.speaking = False
        logger.info(f"ListenSession abort client={client_id} reason={reason}")
        return True

    def remove(self, client_id: str) -> bool:
        with self._lock:
            sess = self._sessions.pop(client_id, None)
        if not sess:
            return False
        if self.vad:
            self.vad.release(sess)
        sess.reset_audio_states()
        sess.aec.clear()
        return True

    def control_listen(
        self,
        client_id: str,
        *,
        state: str,
        mode: str = "",
        text: str = "",
        aec_enabled: bool | None = None,
        message_id: str = "",
    ) -> str:
        sess = self.get_or_create(client_id)
        if mode:
            sess.listen_mode = mode
        if aec_enabled is not None:
            sess.aec_enabled = bool(aec_enabled)

        state = (state or "").lower()
        mid = message_id or uuid.uuid4().hex

        if state == "start":
            sess.listening = True
            sess.reset_audio_states()
            if self.notify_fn:
                self.notify_fn(client_id, mid, "listen_start")
            return "started"

        if state == "stop":
            sess.listening = False
            sess.client_voice_stop = True
            result = self._flush_asr(sess, mid, force=True)
            sess.reset_audio_states()
            if self.notify_fn:
                self.notify_fn(client_id, mid, "listen_stop")
            return result or "stopped"

        if state == "detect":
            sess.reset_audio_states()
            content = (text or "").strip()
            if content and self.forward_text_fn:
                return self.forward_text_fn(client_id, mid, content)
            return "detect"

        return "ignored"

    def push_aec_reference(
        self, client_id: str, pcm: bytes, timestamp: int = 0
    ) -> None:
        sess = self.get_or_create(client_id)
        sess.aec.push(timestamp, pcm)

    def ingest_audio(
        self,
        client_id: str,
        data: bytes,
        *,
        format: str = "opus",
        timestamp: int = 0,
        aec_enabled: bool | None = None,
        listen_mode: str = "",
        message_id: str = "",
    ) -> str:
        if not self.vad:
            raise RuntimeError("ListenSessionStore not configured")
        sess = self.get_or_create(client_id)
        mid = message_id or uuid.uuid4().hex
        if listen_mode:
            sess.listen_mode = listen_mode
        if aec_enabled is not None:
            sess.aec_enabled = bool(aec_enabled)

        pcm = decode_chunk(data, format=format, decoder=sess.decoder)
        if not pcm:
            return ""

        if sess.aec_enabled:
            pcm = sess.aec.apply(pcm, int(timestamp or 0))

        have_voice = bool(self.vad.is_vad(sess, pcm))

        # Barge-in: voice while speaking + AEC → abort peers
        if (
            sess.aec_enabled
            and have_voice
            and sess.speaking
            and sess.listen_mode != "manual"
            and self.abort_peers_fn
        ):
            self.abort_peers_fn(client_id, mid, "barge_in")
            sess.speaking = False

        if sess.listen_mode == "manual":
            sess.asr_audio.append(pcm)
            return ""

        sess.asr_audio.append(pcm)
        if not have_voice and not sess.client_have_voice:
            sess.asr_audio = sess.asr_audio[-self.pre_roll_frames :]
            return ""

        if sess.client_voice_stop:
            if self.notify_fn and sess.client_have_voice:
                self.notify_fn(client_id, mid, "listen_start")
            result = self._flush_asr(sess, mid, force=False)
            sess.reset_audio_states()
            if self.notify_fn:
                self.notify_fn(client_id, mid, "listen_stop")
            return result
        return ""

    def _flush_asr(
        self, sess: ListenSession, message_id: str, *, force: bool
    ) -> str:
        if not self.asr_fn or not self.forward_text_fn:
            return ""
        pcm_bytes = b"".join(sess.asr_audio)
        if not pcm_bytes:
            return ""
        if not force and len(pcm_bytes) < self.min_asr_pcm_bytes:
            logger.debug(
                f"ASR skip short utterance client={sess.client_id} "
                f"bytes={len(pcm_bytes)} < {self.min_asr_pcm_bytes}"
            )
            return ""
        text = self.asr_fn(sess.client_id, message_id, pcm_bytes) or ""
        text = text.strip()
        if not text:
            return ""
        if self.send_device_fn:
            stt = json.dumps(
                {"type": "stt", "text": text},
                ensure_ascii=False,
            )
            self.send_device_fn(sess.client_id, message_id, stt)
        return self.forward_text_fn(sess.client_id, message_id, text)

    def set_speaking(self, client_id: str, speaking: bool) -> None:
        sess = self.get_or_create(client_id)
        sess.speaking = speaking


session_store = ListenSessionStore()
