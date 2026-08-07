"""TTS provider base + factory."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List


class TTSProviderBase(ABC):
    output_format: str = "opus"

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}
        self.sample_rate = int(self.config.get("sample_rate") or 16000)
        self.frame_duration_ms = int(self.config.get("frame_duration_ms") or 60)

    @abstractmethod
    def synthesize_opus_frames(self, text: str) -> List[bytes]:
        raise NotImplementedError
