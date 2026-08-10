"""Per-client speak session: text queue + synthesize + paced downlink."""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from app.core.downlink import Downlink
from app.core.rate_controller import SyncAudioRateController
from app.providers.tts.base import TTSProviderBase


@dataclass
class SpeakJob:
    message_id: str
    text: str
    index: int
    total: int
    end: bool
    emotion: str = "neutral"
    is_broadcast: bool = False


class SpeakSession:
    def __init__(
        self,
        client_id: str,
        tts: TTSProviderBase,
        downlink: Downlink,
        *,
        frame_duration_ms: int = 60,
    ) -> None:
        self.client_id = client_id
        self.tts = tts
        self.downlink = downlink
        self.frame_duration_ms = frame_duration_ms
        self._q: queue.Queue[Optional[SpeakJob]] = queue.Queue()
        self._lock = threading.Lock()
        self._abort = False
        self._turn_started = False
        self._active_message_id = ""
        # While set, only this message_id may enqueue TTS (broadcast ownership).
        self._broadcast_message_id = ""
        self._rate = SyncAudioRateController(frame_duration_ms)
        self._worker = threading.Thread(
            target=self._run, name=f"speak-{client_id[:8]}", daemon=True
        )
        self._worker.start()

    def enqueue(self, job: SpeakJob) -> bool:
        """Enqueue a speak job. Returns False if blocked by an active broadcast."""
        with self._lock:
            broadcast_id = self._broadcast_message_id
            if (
                broadcast_id
                and job.message_id != broadcast_id
                and not job.is_broadcast
            ):
                logger.info(
                    f"SpeakSession reject foreign TTS during broadcast "
                    f"client={self.client_id} active={broadcast_id} "
                    f"got={job.message_id}"
                )
                return False
            if job.is_broadcast and job.message_id:
                self._broadcast_message_id = job.message_id
            if self._abort and not job.end:
                # New speak after abort resets abort latch
                self._abort = False
                self._rate.reset()
            if job.message_id and job.message_id != self._active_message_id:
                self._turn_started = False
                self._active_message_id = job.message_id
                self._rate.reset()
        self._q.put(job)
        return True

    def abort(self, reason: str = "abort") -> None:
        with self._lock:
            self._abort = True
            self._turn_started = False
            # Starting a new broadcast keeps ownership via subsequent enqueue;
            # other aborts must clear a stuck broadcast lease.
            if reason != "broadcast":
                self._broadcast_message_id = ""
        self._rate.abort()
        # Drain pending text jobs
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self.downlink.send_state(
            client_id=self.client_id,
            state="stop",
            message_id=self._active_message_id,
        )
        logger.info(f"SpeakSession abort client={self.client_id} reason={reason}")

    def clear_broadcast(self, message_id: str = "") -> None:
        with self._lock:
            if not message_id or self._broadcast_message_id == message_id:
                self._broadcast_message_id = ""

    def broadcast_active(self) -> bool:
        with self._lock:
            return bool(self._broadcast_message_id)

    def has_active_turn(self) -> bool:
        """True when audio is draining or jobs are still queued for this client."""
        with self._lock:
            if self._turn_started:
                return True
        return not self._q.empty()

    def _should_abort(self) -> bool:
        return self._abort

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                break
            try:
                self._handle(job)
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"SpeakSession error client={self.client_id}: {exc}")

    def _handle(self, job: SpeakJob) -> None:
        if self._should_abort() and not job.end:
            return

        if job.end:
            if self._turn_started and not self._should_abort():
                self.downlink.send_state(
                    client_id=self.client_id,
                    state="stop",
                    message_id=job.message_id,
                    index=job.index,
                    total=job.total or job.index,
                )
            with self._lock:
                self._turn_started = False
                if job.is_broadcast or (
                    self._broadcast_message_id
                    and job.message_id == self._broadcast_message_id
                ):
                    self._broadcast_message_id = ""
            return

        text = (job.text or "").strip()
        if not text:
            return

        if self._should_abort():
            return

        if not self._turn_started:
            self.downlink.send_state(
                client_id=self.client_id,
                state="start",
                message_id=job.message_id,
            )
            with self._lock:
                self._turn_started = True
                self._abort = False
            self._rate.reset()

        if self._should_abort():
            return

        self.downlink.send_state(
            client_id=self.client_id,
            state="sentence_start",
            message_id=job.message_id,
            text=text,
            index=job.index,
            total=job.total,
        )

        frames = self.tts.synthesize_opus_frames(text)
        if self._should_abort():
            return

        fmt = getattr(self.tts, "output_format", "opus") or "opus"
        # Continuous pacing within a turn; reset only on first sentence
        if job.index <= 1:
            self._rate.reset()

        for frame in frames:
            if self._should_abort():
                return
            self._rate.add_audio(frame)

        def _send(frame: bytes) -> None:
            self.downlink.send(
                client_id=self.client_id,
                message_id=job.message_id,
                audio=frame,
                text="",
                index=job.index,
                total=job.total,
                format=fmt,
            )

        self._rate.drain(_send, should_abort=self._should_abort)


class SpeakSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SpeakSession] = {}
        self._lock = threading.Lock()
        self.tts: Optional[TTSProviderBase] = None
        self.downlink: Optional[Downlink] = None
        self.frame_duration_ms = 60

    def configure(
        self,
        tts: TTSProviderBase,
        downlink: Downlink,
        *,
        frame_duration_ms: int = 60,
    ) -> None:
        self.tts = tts
        self.downlink = downlink
        self.frame_duration_ms = frame_duration_ms

    def get_or_create(self, client_id: str) -> SpeakSession:
        if not self.tts or not self.downlink:
            raise RuntimeError("SpeakSessionStore not configured")
        with self._lock:
            sess = self._sessions.get(client_id)
            if sess is None:
                sess = SpeakSession(
                    client_id,
                    self.tts,
                    self.downlink,
                    frame_duration_ms=self.frame_duration_ms,
                )
                self._sessions[client_id] = sess
            else:
                sess.tts = self.tts
            return sess

    def abort(self, client_id: str, reason: str = "abort") -> bool:
        with self._lock:
            sess = self._sessions.get(client_id)
        if not sess:
            return False
        sess.abort(reason)
        return True

    def get(self, client_id: str) -> Optional[SpeakSession]:
        with self._lock:
            return self._sessions.get(client_id)

    def speak_text(
        self,
        client_id: str,
        text: str,
        *,
        message_id: str = "",
        index: int = 1,
        total: int = 0,
        end: bool = False,
        emotion: str = "neutral",
        is_broadcast: bool = False,
    ) -> bool:
        sess = self.get_or_create(client_id)
        return sess.enqueue(
            SpeakJob(
                message_id=message_id or uuid.uuid4().hex,
                text=text,
                index=index,
                total=total,
                end=end,
                emotion=emotion,
                is_broadcast=is_broadcast,
            )
        )


session_store = SpeakSessionStore()
