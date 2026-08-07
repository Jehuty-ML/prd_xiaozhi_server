from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Protocol


class VadClientState(Protocol):
    """Minimal per-client state VAD providers mutate."""

    listen_mode: str
    client_audio_buffer: bytearray
    client_voice_window: list
    client_have_voice: bool
    client_voice_stop: bool
    last_is_voice: bool
    vad_last_voice_time: float


class VADProviderBase(ABC):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    @abstractmethod
    def is_vad(self, state: VadClientState, pcm_frame: bytes) -> bool:
        """Return whether current frame window has voice; may set voice_stop."""

    def release(self, state: VadClientState) -> None:  # noqa: ARG002
        return
