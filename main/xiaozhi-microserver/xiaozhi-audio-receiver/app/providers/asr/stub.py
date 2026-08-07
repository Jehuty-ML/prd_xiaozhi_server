"""Dev / CI ASR — deterministic text when PCM is long enough."""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.providers.asr.base import ASRProviderBase


class StubASR(ASRProviderBase):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.fixed_text = str(self.config.get("fixed_text") or "你好")
        self.min_pcm_bytes = int(self.config.get("min_pcm_bytes") or 9600)

    def recognize(self, pcm: bytes, *, sample_rate: int = 16000) -> str:
        pcm = pcm or b""
        if len(pcm) < self.min_pcm_bytes:
            logger.debug(
                f"StubASR skip short pcm={len(pcm)} < min={self.min_pcm_bytes}"
            )
            return ""
        logger.info(f"StubASR pcm={len(pcm)} sr={sample_rate} -> {self.fixed_text!r}")
        return self.fixed_text
