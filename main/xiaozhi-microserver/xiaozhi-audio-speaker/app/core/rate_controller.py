"""Sync ~60ms audio frame rate controller (thread-friendly)."""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, Optional


class SyncAudioRateController:
    """Pace Opus frames at frame_duration_ms without asyncio."""

    def __init__(self, frame_duration_ms: int = 60) -> None:
        self.frame_duration_ms = max(1, int(frame_duration_ms))
        self.queue: deque = deque()
        self.play_position_ms = 0
        self.start_timestamp: Optional[float] = None
        self._aborted = False

    def reset(self) -> None:
        self.queue.clear()
        self.play_position_ms = 0
        self.start_timestamp = None
        self._aborted = False

    def abort(self) -> None:
        self._aborted = True
        self.queue.clear()

    def add_audio(self, frame: bytes) -> None:
        if self._aborted:
            return
        self.queue.append(frame)

    def drain(
        self,
        send_fn: Callable[[bytes], None],
        *,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> int:
        """Send queued frames with pacing. Returns frames sent."""
        sent = 0
        while self.queue:
            if self._aborted or (should_abort and should_abort()):
                self.queue.clear()
                break
            frame = self.queue.popleft()
            if self.start_timestamp is None:
                self.start_timestamp = time.monotonic()
            target = self.play_position_ms
            elapsed_ms = (time.monotonic() - self.start_timestamp) * 1000
            wait_ms = target - elapsed_ms
            if wait_ms > 1:
                time.sleep(wait_ms / 1000.0)
            if self._aborted or (should_abort and should_abort()):
                self.queue.clear()
                break
            send_fn(frame)
            sent += 1
            self.play_position_ms += self.frame_duration_ms
        return sent
