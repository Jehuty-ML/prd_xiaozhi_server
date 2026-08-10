"""Silero ONNX VAD (optional — falls back if model/onnxruntime missing)."""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from typing import Any

from loguru import logger

from app.providers.vad.base import VADProviderBase, VadClientState

try:
    import numpy as np
    import onnxruntime

    _HAS_ORT = True
except Exception:  # noqa: BLE001
    np = None  # type: ignore
    onnxruntime = None  # type: ignore
    _HAS_ORT = False


class SileroVAD(VADProviderBase):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        if not _HAS_ORT:
            raise RuntimeError("onnxruntime / numpy required for SileroVAD")

        model_path = self._resolve_model_path()
        if not model_path.exists():
            raise FileNotFoundError(f"Silero ONNX not found: {model_path}")

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"], sess_options=opts
        )
        self.vad_threshold = float(self.config.get("threshold") or 0.5)
        self.vad_threshold_low = float(self.config.get("threshold_low") or 0.2)
        self.silence_threshold_ms = int(
            self.config.get("min_silence_duration_ms") or 200
        )
        self.frame_window_threshold = 3
        logger.info(f"SileroVAD loaded model={model_path}")

    def _resolve_model_path(self) -> Path:
        model_dir = self.config.get("model_dir") or ""
        explicit = self.config.get("model_path")
        if explicit:
            return Path(str(explicit))

        service_root = Path(__file__).resolve().parents[3]
        # microserver/xiaozhi-audio-preprocess → microserver/models
        shared_models = (service_root.parent / "models").resolve()

        bases: list[Path] = []
        raw = Path(str(model_dir)) if model_dir else Path()
        if raw.is_absolute():
            bases.append(raw)
        elif model_dir:
            bases.append((service_root / raw).resolve())
            # manager-api may ship paths like "models/snakers4_silero-vad"
            parts = raw.parts
            if parts and parts[0] == "models":
                bases.append((shared_models.joinpath(*parts[1:])).resolve())
            elif raw.name:
                bases.append((shared_models / raw.name).resolve())
        else:
            bases.append(shared_models / "snakers4_silero-vad")

        last_candidate = Path()
        for base in bases:
            candidate = base / "src" / "silero_vad" / "data" / "silero_vad.onnx"
            if candidate.exists():
                return candidate
            direct = base / "silero_vad.onnx"
            if direct.exists():
                return direct
            last_candidate = candidate
        return last_candidate

    def _ensure_state(self, state: VadClientState) -> None:
        if not hasattr(state, "_vad_state") or state._vad_state is None:  # type: ignore[attr-defined]
            state._vad_state = np.zeros((2, 1, 128), dtype=np.float32)  # type: ignore[attr-defined]
        if not hasattr(state, "_vad_context") or state._vad_context is None:  # type: ignore[attr-defined]
            state._vad_context = np.zeros((1, 64), dtype=np.float32)  # type: ignore[attr-defined]
        if not isinstance(state.client_voice_window, deque):
            state.client_voice_window = deque(maxlen=5)  # type: ignore[assignment]

    def release(self, state: VadClientState) -> None:
        for attr in ("_vad_state", "_vad_context"):
            if hasattr(state, attr):
                try:
                    delattr(state, attr)
                except Exception:  # noqa: BLE001
                    pass

    def is_vad(self, state: VadClientState, pcm_frame: bytes) -> bool:
        if state.listen_mode == "manual":
            return True
        try:
            self._ensure_state(state)
            state.client_audio_buffer.extend(pcm_frame)
            client_have_voice = False
            while len(state.client_audio_buffer) >= 512 * 2:
                chunk = bytes(state.client_audio_buffer[: 512 * 2])
                del state.client_audio_buffer[: 512 * 2]
                audio_int16 = np.frombuffer(chunk, dtype=np.int16)
                audio_float32 = audio_int16.astype(np.float32) / 32768.0
                audio_input = np.concatenate(
                    [state._vad_context, audio_float32.reshape(1, -1)],  # type: ignore[attr-defined]
                    axis=1,
                ).astype(np.float32)
                ort_inputs = {
                    "input": audio_input,
                    "state": state._vad_state,  # type: ignore[attr-defined]
                    "sr": np.array(16000, dtype=np.int64),
                }
                out, new_state = self.session.run(None, ort_inputs)
                state._vad_state = new_state  # type: ignore[attr-defined]
                state._vad_context = audio_input[:, -64:]  # type: ignore[attr-defined]
                speech_prob = float(out.item())

                if speech_prob >= self.vad_threshold:
                    is_voice = True
                elif speech_prob <= self.vad_threshold_low:
                    is_voice = False
                else:
                    is_voice = state.last_is_voice

                state.last_is_voice = is_voice
                state.client_voice_window.append(is_voice)
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
        except Exception as exc:  # noqa: BLE001
            logger.error(f"SileroVAD error: {exc}")
            return False
