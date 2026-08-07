"""Edge TTS provider (Microsoft Edge online voices)."""

from __future__ import annotations

import asyncio
from typing import List

from loguru import logger

from app.providers.tts.audio_codec import audio_bytes_to_opus_frames
from app.providers.tts.base import TTSProviderBase


class EdgeTTS(TTSProviderBase):
    output_format = "opus"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        cfg = self.config
        self.voice = cfg.get("private_voice") or cfg.get("voice") or "zh-CN-XiaoxiaoNeural"
        self.audio_file_type = cfg.get("format") or "mp3"
        volume = cfg.get("volume", "50")
        speech_rate = cfg.get("rate", "0")
        pitch_rate = cfg.get("pitch", "0")
        self.edge_rate = f"{int(speech_rate) if speech_rate else 0:+}%"
        vol = int(volume) if volume else 50
        self.edge_volume = f"{vol - 50:+}%"
        self.edge_pitch = f"{int(pitch_rate) if pitch_rate else 0:+}Hz"

    def synthesize_opus_frames(self, text: str) -> List[bytes]:
        text = (text or "").strip()
        if not text:
            return []
        try:
            audio = self._fetch_audio(text)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"EdgeTTS failed: {exc}")
            return []
        return audio_bytes_to_opus_frames(
            audio,
            file_type=self.audio_file_type,
            sample_rate=self.sample_rate,
            frame_ms=self.frame_duration_ms,
        )

    def _fetch_audio(self, text: str) -> bytes:
        import edge_tts

        async def _run() -> bytes:
            communicate = edge_tts.Communicate(
                text,
                voice=self.voice,
                rate=self.edge_rate,
                volume=self.edge_volume,
                pitch=self.edge_pitch,
            )
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return asyncio.run(_run())
            return loop.run_until_complete(_run())
        except RuntimeError:
            return asyncio.run(_run())
