"""OpenAI-compatible transcription ASR (Whisper-style multipart upload)."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any

from loguru import logger

from app.providers.asr.base import ASRProviderBase


class OpenAICompatASR(ASRProviderBase):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.api_key = str(self.config.get("api_key") or "")
        self.url = str(
            self.config.get("url")
            or "https://api.openai.com/v1/audio/transcriptions"
        )
        self.model = str(self.config.get("model_name") or "whisper-1")
        self.language = str(self.config.get("language") or "")
        out = self.config.get("output_dir") or "tmp/"
        self.output_dir = Path(out)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def recognize(self, pcm: bytes, *, sample_rate: int = 16000) -> str:
        pcm = pcm or b""
        if not pcm:
            return ""
        if not self.api_key:
            logger.warning("OpenAICompatASR missing api_key")
            return ""
        try:
            import httpx
        except ImportError as exc:
            logger.error(f"httpx required for OpenAICompatASR: {exc}")
            return ""

        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        wav_bytes = wav_buf.getvalue()

        headers = {"Authorization": f"Bearer {self.api_key}"}
        data: dict[str, Any] = {"model": self.model}
        if self.language:
            data["language"] = self.language
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(self.url, headers=headers, data=data, files=files)
            if resp.status_code != 200:
                logger.error(
                    f"OpenAICompatASR HTTP {resp.status_code}: {resp.text[:200]}"
                )
                return ""
            body = resp.json()
            text = body.get("text") or ""
            logger.info(f"OpenAICompatASR -> {text!r}")
            return str(text).strip()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"OpenAICompatASR failed: {exc}")
            return ""
