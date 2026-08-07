"""Dev fallback TTS — silent Opus frames, no external API."""

from __future__ import annotations

from typing import List

from loguru import logger

from app.providers.tts.audio_codec import silence_opus_frames
from app.providers.tts.base import TTSProviderBase


class EchoTTS(TTSProviderBase):
    output_format = "opus"

    def synthesize_opus_frames(self, text: str) -> List[bytes]:
        # ~180ms silence so smoke / device protocol can observe frames
        frames = silence_opus_frames(
            count=3,
            sample_rate=self.sample_rate,
            frame_ms=self.frame_duration_ms,
        )
        logger.debug(
            f"EchoTTS text={text!r} frames={len(frames)} sample_rate={self.sample_rate}"
        )
        return frames
