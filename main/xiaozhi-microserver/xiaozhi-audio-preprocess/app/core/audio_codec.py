"""Opus ↔ PCM helpers for preprocess."""

from __future__ import annotations

from typing import Optional

from loguru import logger

try:
    from opuslib_next import Decoder

    _HAS_OPUS = True
except Exception:  # noqa: BLE001
    Decoder = None  # type: ignore
    _HAS_OPUS = False


class OpusDecoderSession:
    """Per-client Opus decoder (16 kHz mono, 60ms → 960 samples)."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_size = 960  # 60ms @ 16kHz
        self._decoder: Optional[object] = None
        if _HAS_OPUS:
            self._decoder = Decoder(sample_rate, channels)

    def decode(self, packet: bytes) -> bytes:
        if not packet:
            return b""
        if not self._decoder:
            # Dev fallback: treat as raw PCM
            return packet
        try:
            return self._decoder.decode(packet, self.frame_size)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Opus decode failed ({exc}); treating as PCM")
            return packet


def decode_chunk(
    data: bytes,
    *,
    format: str,
    decoder: OpusDecoderSession,
) -> bytes:
    fmt = (format or "raw").lower()
    if fmt in ("pcm",):
        return data
    if fmt in ("opus", "raw"):
        # Devices typically send Opus; "raw" historically meant opaque uplink bytes.
        # Try Opus first; if decoder falls back it returns original bytes.
        return decoder.decode(data)
    return data
