"""Lightweight spectral-subtraction AEC using TTS reference cache."""

from __future__ import annotations

import time
from typing import Dict

from loguru import logger

try:
    import numpy as np

    _HAS_NP = True
except Exception:  # noqa: BLE001
    np = None  # type: ignore
    _HAS_NP = False


class AecCache:
    """Per-client reference PCM frames keyed by playback timestamp (ms)."""

    def __init__(self, *, ttl_sec: float = 5.0, max_frames: int = 200) -> None:
        self.ttl_sec = ttl_sec
        self.max_frames = max_frames
        self._frames: Dict[int, bytes] = {}
        self._times: Dict[int, float] = {}

    def push(self, timestamp: int, pcm: bytes) -> None:
        if not pcm:
            return
        ts = int(timestamp) if timestamp else int(time.time() * 1000)
        self._frames[ts] = bytes(pcm)
        self._times[ts] = time.time()
        self._prune()

    def clear(self) -> None:
        self._frames.clear()
        self._times.clear()

    def _prune(self) -> None:
        now = time.time()
        expired = [ts for ts, t0 in self._times.items() if now - t0 > self.ttl_sec]
        for ts in expired:
            self._frames.pop(ts, None)
            self._times.pop(ts, None)
        if len(self._frames) > self.max_frames:
            for ts in sorted(self._frames.keys())[: len(self._frames) - self.max_frames]:
                self._frames.pop(ts, None)
                self._times.pop(ts, None)

    def apply(self, pcm_frame: bytes, timestamp: int = 0) -> bytes:
        if not _HAS_NP or not pcm_frame or not self._frames:
            return pcm_frame
        try:
            return _apply_aec(pcm_frame, self._frames, timestamp)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"AEC apply skipped: {exc}")
            return pcm_frame


def _apply_aec(
    pcm_frame: bytes, cache: Dict[int, bytes], timestamp: int
) -> bytes:
    mic_audio = np.frombuffer(pcm_frame, dtype=np.int16).astype(np.float32)
    mic_rms = float(np.sqrt(np.mean(mic_audio**2)))
    if mic_rms < 100:
        return pcm_frame

    sorted_ts = sorted(cache.keys())
    if len(sorted_ts) < 2:
        return pcm_frame

    n = len(mic_audio)
    if timestamp > 0:
        closest_idx = min(
            range(len(sorted_ts)), key=lambda i: abs(sorted_ts[i] - timestamp)
        )
    else:
        closest_idx = len(sorted_ts) - 1

    mic_window = np.hanning(n)
    mic_fft = np.fft.rfft(mic_audio * mic_window)
    mic_psd = np.abs(mic_fft) ** 2
    mic_log_psd = 10 * np.log10(mic_psd + 1e-8)
    mic_p_xx = float(np.dot(mic_log_psd, mic_log_psd))

    best_corr = -1.0
    best_ref_idx = closest_idx
    best_ref_rms = 0.0
    for offset in range(-2, 3):
        test_idx = closest_idx + offset
        if test_idx < 0 or test_idx >= len(sorted_ts):
            continue
        test_ts = sorted_ts[test_idx]
        test_ref = np.frombuffer(cache[test_ts], dtype=np.int16).astype(np.float32)
        test_ref_rms = float(np.sqrt(np.mean(test_ref**2)))
        if test_ref_rms < 50:
            continue
        test_window = np.hanning(len(test_ref))
        test_fft = np.fft.rfft(test_ref * test_window)
        test_psd = np.abs(test_fft) ** 2
        test_log_psd = 10 * np.log10(test_psd + 1e-8)
        p_xy = float(np.dot(mic_log_psd, test_log_psd))
        p_yy = float(np.dot(test_log_psd, test_log_psd))
        corr = abs(p_xy) / ((mic_p_xx**0.5) * (p_yy**0.5) + 1e-8)
        if corr > best_corr:
            best_corr = corr
            best_ref_idx = test_idx
            best_ref_rms = test_ref_rms

    best_ts = sorted_ts[best_ref_idx]
    best_ref = np.frombuffer(cache[best_ts], dtype=np.int16).astype(np.float32)
    if best_ref_rms < 50:
        return pcm_frame

    aligned_ref = best_ref[:n]
    if len(aligned_ref) < n:
        aligned_ref = np.pad(aligned_ref, (0, n - len(aligned_ref)))

    mic_mag = np.abs(mic_fft)
    mic_phase = np.angle(mic_fft)
    ref_fft = np.fft.rfft(aligned_ref * np.hanning(n))
    ref_mag = np.abs(ref_fft)
    scale = float(np.sum(mic_mag * ref_mag) / (np.dot(ref_mag, ref_mag) + 1e-8))
    raw_coef = 1.0 + scale * 3 + (best_corr - 0.97) * 30
    coef = max(0.5, min(3.0, raw_coef))
    echo_mag = ref_mag * scale * coef
    result_mag = np.maximum(mic_mag - echo_mag * 1.5, mic_mag * 0.1)
    result_fft = result_mag * np.exp(1j * mic_phase)
    output = np.fft.irfft(result_fft, n)
    if best_corr >= 0.97 and best_ref_rms > 500:
        output = output * 0.3
    output = np.clip(output, -32768, 32767).astype(np.int16)
    return output.tobytes()
