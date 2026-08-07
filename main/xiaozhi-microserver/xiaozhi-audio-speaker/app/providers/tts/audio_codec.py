"""Minimal Opus PCM encoder + audio bytes → opus frames."""

from __future__ import annotations

from io import BytesIO
from typing import Callable, List, Optional

from loguru import logger

try:
    import numpy as np
    from opuslib_next import Encoder
    from opuslib_next import constants as opus_constants

    _HAS_OPUS = True
except Exception:  # noqa: BLE001
    np = None  # type: ignore
    Encoder = None  # type: ignore
    opus_constants = None  # type: ignore
    _HAS_OPUS = False


class OpusEncoderUtils:
    def __init__(self, sample_rate: int = 16000, channels: int = 1, frame_size_ms: int = 60):
        if not _HAS_OPUS:
            raise RuntimeError("opuslib_next / numpy not available")
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_size_ms = frame_size_ms
        self.frame_size = (sample_rate * frame_size_ms) // 1000
        self.total_frame_size = self.frame_size * channels
        self.buffer = np.array([], dtype=np.int16)
        self.encoder = Encoder(sample_rate, channels, opus_constants.APPLICATION_AUDIO)
        self.encoder.bitrate = 24000
        self.encoder.complexity = 10

    def reset_state(self) -> None:
        self.encoder.reset_state()
        self.buffer = np.array([], dtype=np.int16)

    def encode_pcm_to_opus_stream(
        self, pcm_data: bytes, end_of_stream: bool, callback: Callable[[bytes], None]
    ) -> None:
        new_samples = np.frombuffer(pcm_data, dtype=np.int16)
        self.buffer = np.append(self.buffer, new_samples)
        offset = 0
        while offset <= len(self.buffer) - self.total_frame_size:
            frame = self.buffer[offset : offset + self.total_frame_size]
            encoded = self.encoder.encode(frame.tobytes(), self.frame_size)
            if encoded:
                callback(encoded)
            offset += self.total_frame_size
        self.buffer = self.buffer[offset:]
        if end_of_stream and len(self.buffer) > 0:
            last = np.zeros(self.total_frame_size, dtype=np.int16)
            last[: len(self.buffer)] = self.buffer
            encoded = self.encoder.encode(last.tobytes(), self.frame_size)
            if encoded:
                callback(encoded)
            self.buffer = np.array([], dtype=np.int16)


def pcm_to_opus_frames(
    pcm: bytes, *, sample_rate: int = 16000, frame_ms: int = 60
) -> List[bytes]:
    frames: List[bytes] = []
    if not pcm:
        return frames
    if not _HAS_OPUS:
        # Fallback: chunk raw pcm as pseudo-frames (dev only)
        frame_bytes = int(sample_rate * frame_ms / 1000) * 2
        for i in range(0, len(pcm), frame_bytes):
            chunk = pcm[i : i + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk += b"\x00" * (frame_bytes - len(chunk))
            frames.append(chunk)
        return frames
    enc = OpusEncoderUtils(sample_rate=sample_rate, frame_size_ms=frame_ms)
    frame_bytes = enc.frame_size * 2
    for i in range(0, len(pcm), frame_bytes):
        chunk = pcm[i : i + frame_bytes]
        is_last = i + frame_bytes >= len(pcm)
        if len(chunk) < frame_bytes and not is_last:
            continue
        enc.encode_pcm_to_opus_stream(chunk, end_of_stream=is_last, callback=frames.append)
    return frames


def audio_bytes_to_opus_frames(
    audio_bytes: bytes,
    *,
    file_type: str = "mp3",
    sample_rate: int = 16000,
    frame_ms: int = 60,
) -> List[bytes]:
    if not audio_bytes:
        return []
    try:
        from pydub import AudioSegment
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"pydub unavailable, cannot decode {file_type}: {exc}")
        return []
    audio = AudioSegment.from_file(
        BytesIO(audio_bytes), format=file_type, parameters=["-nostdin"]
    )
    audio = audio.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)
    return pcm_to_opus_frames(audio.raw_data, sample_rate=sample_rate, frame_ms=frame_ms)


def silence_opus_frames(
    *, count: int = 3, sample_rate: int = 16000, frame_ms: int = 60
) -> List[bytes]:
    samples = int(sample_rate * frame_ms / 1000) * count
    pcm = b"\x00" * (samples * 2)
    return pcm_to_opus_frames(pcm, sample_rate=sample_rate, frame_ms=frame_ms)
