"""Energy-based VAD for CI / default (no ONNX dependency)."""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from app.providers.vad.base import VADProviderBase, VadClientState

try:
    import numpy as np

    _HAS_NP = True
except Exception:  # noqa: BLE001
    np = None  # type: ignore
    _HAS_NP = False


class StubVAD(VADProviderBase):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.energy_threshold = float(self.config.get("energy_threshold") or 200)
        self.silence_threshold_ms = int(
            self.config.get("min_silence_duration_ms") or 400
        )
        self.frame_window_threshold = int(
            self.config.get("frame_window_threshold") or 3
        )
        self.chunk_samples = 512

    def is_vad(self, state: VadClientState, pcm_frame: bytes) -> bool:
        if state.listen_mode == "manual":
            return True

        state.client_audio_buffer.extend(pcm_frame)
        client_have_voice = False
        chunk_bytes = self.chunk_samples * 2

        while len(state.client_audio_buffer) >= chunk_bytes:
            chunk = bytes(state.client_audio_buffer[:chunk_bytes])
            del state.client_audio_buffer[:chunk_bytes]
            is_voice = self._chunk_is_voice(chunk)

            if not isinstance(state.client_voice_window, deque):
                state.client_voice_window = deque(maxlen=5)  # type: ignore[assignment]
            state.client_voice_window.append(is_voice)
            state.last_is_voice = is_voice
            client_have_voice = (
                list(state.client_voice_window).count(True)
                >= self.frame_window_threshold
            )

            if state.client_have_voice and not client_have_voice:
                stop_duration = time.time() * 1000 - state.vad_last_voice_time
                if stop_duration >= self.silence_threshold_ms:
                    state.client_voice_stop = True
            if client_have_voice:
                state.client_have_voice = True
                state.vad_last_voice_time = time.time() * 1000

        return client_have_voice

    def _chunk_is_voice(self, chunk: bytes) -> bool:
        if not chunk:
            return False
        if _HAS_NP:
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples**2))) if len(samples) else 0.0
        else:
            # Rough RMS without numpy
            n = len(chunk) // 2
            if n == 0:
                return False
            total = 0.0
            for i in range(0, len(chunk) - 1, 2):
                sample = int.from_bytes(chunk[i : i + 2], "little", signed=True)
                total += sample * sample
            rms = (total / n) ** 0.5
        return rms >= self.energy_threshold
