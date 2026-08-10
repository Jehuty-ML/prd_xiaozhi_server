"""Session API — replaces monolith ConnectionHandler for agent/plugins."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc, command_pb2, command_pb2_grpc
from xiaozhi_common.constants import ACCESS_SERVICE, SPEAKER_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.session import SessionState

from app.core.dialogue import Dialogue, Message


@dataclass
class Session:
    """Per-device dialogue session owned by xiaozhi-agent.

    Plugins receive this instead of ConnectionHandler / websocket.
    Outbound TTS goes through speaker; MCP/IoT frames go through access proxy.
    """

    client_id: str
    pool: GrpcClientPool
    config: dict[str, Any]
    session_id: str = ""
    device_id: str = ""
    prompt: str = ""
    dialogue: Dialogue = field(default_factory=Dialogue)
    sentence_id: str = ""
    intent_type: str = "function_call"
    state: SessionState = SessionState.IDLE
    abort_reason: Optional[str] = None
    client_abort: bool = False
    close_after_chat: bool = False
    features: dict[str, Any] = field(default_factory=dict)
    iot_descriptors: dict[str, Any] = field(default_factory=dict)
    mcp_tools: dict[str, Any] = field(default_factory=dict)
    mcp_name_mapping: dict[str, str] = field(default_factory=dict)
    mcp_call_futures: dict[int, Any] = field(default_factory=dict)
    mcp_next_id: int = 1
    extras: dict[str, Any] = field(default_factory=dict)
    llm: Any = None
    _speak_index: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = uuid.uuid4().hex
        if not self.device_id:
            self.device_id = self.client_id

    def reset_abort(self) -> None:
        with self._lock:
            self.client_abort = False
            self.abort_reason = None
            if self.state == SessionState.ABORTED:
                self.state = SessionState.IDLE

    def mark_abort(self, reason: str = "abort") -> None:
        with self._lock:
            self.client_abort = True
            self.abort_reason = reason
            self.state = SessionState.ABORTED

    def change_system_prompt(self, prompt: str) -> None:
        self.prompt = prompt
        self.dialogue.update_system_message(prompt)

    def speak(self, text: str, *, emotion: str = "neutral", index: int = 0, total: int = 0) -> None:
        """Enqueue one TTS sentence via audio-speaker."""
        text = (text or "").strip()
        if not text:
            return
        if self.client_abort:
            return
        if index <= 0:
            self._speak_index += 1
            index = self._speak_index
        # total<=0 means non-final; speaker waits for speak_end()
        message_id = self.sentence_id or uuid.uuid4().hex
        stub = audio_pb2_grpc.AudioSpeakerServiceStub(self.pool.channel(SPEAKER_SERVICE))
        resp = stub.SpeakText(
            audio_pb2.SpeakRequest(
                message_id=message_id,
                client_id=self.client_id,
                text=text,
                emotion=emotion,
                index=index,
                total=total if total > 0 else 0,
                end=False,
            ),
            metadata=self.pool.metadata(self.client_id, message_id),
            timeout=30,
        )
        if int(getattr(resp, "code", 0) or 0) == 2:
            # Speaker owns a broadcast lease; stop injecting dialogue TTS.
            self.mark_abort("blocked_by_broadcast")
            logger.info(
                f"Session.speak blocked by broadcast client={self.client_id}"
            )
            return
        logger.debug(f"Session.speak client={self.client_id} idx={index} text={text!r}")

    def speak_end(self) -> None:
        """Signal end of current speak turn (TTS state=stop after drain)."""
        if self.client_abort:
            return
        message_id = self.sentence_id or uuid.uuid4().hex
        index = self._speak_index or 1
        stub = audio_pb2_grpc.AudioSpeakerServiceStub(self.pool.channel(SPEAKER_SERVICE))
        resp = stub.SpeakText(
            audio_pb2.SpeakRequest(
                message_id=message_id,
                client_id=self.client_id,
                text="",
                emotion="neutral",
                index=index,
                total=index,
                end=True,
            ),
            metadata=self.pool.metadata(self.client_id, message_id),
            timeout=10,
        )
        if int(getattr(resp, "code", 0) or 0) == 2:
            self.mark_abort("blocked_by_broadcast")
            return
        logger.debug(f"Session.speak_end client={self.client_id} idx={index}")

    def send_device_json(self, payload: dict[str, Any]) -> bool:
        """Proxy a JSON frame to the device WebSocket via access."""
        import json

        message_id = uuid.uuid4().hex
        stub = command_pb2_grpc.AccessDeviceProxyServiceStub(
            self.pool.channel(ACCESS_SERVICE)
        )
        resp = stub.SendToDevice(
            command_pb2.DeviceMessageRequest(
                message_id=message_id,
                client_id=self.client_id,
                json_text=json.dumps(payload, ensure_ascii=False),
            ),
            metadata=self.pool.metadata(self.client_id, message_id),
            timeout=10,
        )
        return int(resp.code) == 0

    def request_close(self) -> None:
        """Ask access to close / idle the device connection after chat."""
        self.close_after_chat = True
        message_id = uuid.uuid4().hex
        stub = command_pb2_grpc.AccessCommandServiceStub(
            self.pool.channel(ACCESS_SERVICE)
        )
        stub.SendCommand(
            command_pb2.CommandRequest(
                message_id=message_id,
                client_id=self.client_id,
                command="close_after_chat",
                payload="",
            ),
            metadata=self.pool.metadata(self.client_id, message_id),
            timeout=5,
        )


class SessionStore:
    """In-memory session map keyed by client_id."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._chat_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def chat_lock(self, client_id: str) -> threading.Lock:
        with self._lock:
            lock = self._chat_locks.get(client_id)
            if lock is None:
                lock = threading.Lock()
                self._chat_locks[client_id] = lock
            return lock

    def get_or_create(
        self,
        client_id: str,
        pool: GrpcClientPool,
        config: dict[str, Any],
        *,
        prompt: str = "",
        intent_type: str = "function_call",
        device_id: str = "",
        llm: Any = None,
    ) -> Session:
        with self._lock:
            session = self._sessions.get(client_id)
            if session is None:
                session = Session(
                    client_id=client_id,
                    pool=pool,
                    config=config,
                    prompt=prompt,
                    intent_type=intent_type,
                    device_id=device_id or client_id,
                    llm=llm,
                )
                if prompt:
                    session.dialogue.put(Message(role="system", content=prompt))
                self._sessions[client_id] = session
            else:
                session.config = config
                if device_id:
                    session.device_id = device_id
                if llm is not None:
                    session.llm = llm
                if prompt and prompt != session.prompt:
                    session.change_system_prompt(prompt)
                session.intent_type = intent_type
            return session

    def apply_private_config(
        self,
        client_id: str,
        pool: GrpcClientPool,
        private_config: dict[str, Any],
        *,
        device_id: str = "",
        base_config: dict[str, Any] | None = None,
        prompt: str = "",
        intent_type: str = "function_call",
        llm: Any = None,
    ) -> Session:
        """Create/update session with device private config overlay."""
        return self.get_or_create(
            client_id,
            pool,
            private_config if private_config else (base_config or {}),
            prompt=prompt,
            intent_type=intent_type,
            device_id=device_id,
            llm=llm,
        )

    def get(self, client_id: str) -> Optional[Session]:
        return self._sessions.get(client_id)

    def abort(self, client_id: str, reason: str = "abort") -> bool:
        session = self.get(client_id)
        if not session:
            return False
        session.mark_abort(reason)
        return True


session_store = SessionStore()
