from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ASRProviderBase(ABC):
    """Batch PCM → text ASR provider."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    @abstractmethod
    def recognize(self, pcm: bytes, *, sample_rate: int = 16000) -> str:
        """Return transcript text (may be empty)."""
