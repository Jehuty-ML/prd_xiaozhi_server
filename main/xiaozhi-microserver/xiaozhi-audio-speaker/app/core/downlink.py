"""gRPC downlink helper: speaker → access SendTtsAudio."""

from __future__ import annotations

from typing import Optional

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc
from xiaozhi_common.constants import ACCESS_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool


class Downlink:
    def __init__(self, pool: GrpcClientPool) -> None:
        self.pool = pool

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
    ) -> bool:
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
                ),
                metadata=self.pool.metadata(client_id, message_id),
                timeout=10,
            )
            return int(resp.code) == 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Downlink SendTtsAudio failed client={client_id}: {exc}")
            return False

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
