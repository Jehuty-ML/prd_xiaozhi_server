"""gRPC downlink helper: speaker → access SendTtsAudio (+ optional AEC ref)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc
from xiaozhi_common.constants import ACCESS_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool

if TYPE_CHECKING:
    from app.core.aec_uplink import AecReferencePusher


class Downlink:
    def __init__(
        self,
        pool: GrpcClientPool,
        *,
        aec_pusher: Optional["AecReferencePusher"] = None,
        push_aec: bool = True,
    ) -> None:
        self.pool = pool
        self.aec_pusher = aec_pusher
        self.push_aec = push_aec

    def send(
        self,
        *,
        client_id: str,
        message_id: str = "",
        audio: bytes = b"",
        text: str = "",
        index: int = 0,
        total: int = 0,
        format: str = "opus",
        state: str = "",
        session_id: str = "",
        timestamp: int = 0,
    ) -> bool:
        ts = int(timestamp) if timestamp else (int(time.time() * 1000) if audio else 0)
        try:
            stub = audio_pb2_grpc.AccessAudioServiceStub(
                self.pool.channel(ACCESS_SERVICE)
            )
            resp = stub.SendTtsAudio(
                audio_pb2.TtsAudioRequest(
                    message_id=message_id,
                    client_id=client_id,
                    audio=audio or b"",
                    text=text or "",
                    index=index,
                    total=total,
                    format=format or "opus",
                    state=state or "",
                    session_id=session_id or "",
                    timestamp=ts,
                ),
                metadata=self.pool.metadata(client_id, message_id),
                timeout=10,
            )
            ok = int(resp.code) == 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Downlink SendTtsAudio failed client={client_id}: {exc}")
            return False

        # Always push reference when we have audio; preprocess applies only if aec_enabled
        if (
            ok
            and self.push_aec
            and self.aec_pusher
            and audio
            and (format or "opus").lower() in ("opus", "echo", "")
        ):
            self.aec_pusher.push_opus_frame(
                client_id=client_id,
                opus_frame=audio,
                message_id=message_id,
                timestamp=ts,
            )
        return ok

    def send_state(
        self,
        *,
        client_id: str,
        state: str,
        message_id: str = "",
        text: str = "",
        index: int = 0,
        total: int = 0,
        session_id: str = "",
    ) -> bool:
        return self.send(
            client_id=client_id,
            message_id=message_id,
            text=text,
            index=index,
            total=total,
            format="",
            state=state,
            session_id=session_id,
        )
