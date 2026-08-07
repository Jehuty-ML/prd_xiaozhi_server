from __future__ import annotations

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc
from xiaozhi_common.constants import SPEAKER_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool


class AgentServicer(audio_pb2_grpc.AgentServiceServicer):
    """Phase-1 stub agent: echo text to speaker."""

    def __init__(self, pool: GrpcClientPool) -> None:
        self.pool = pool

    def SendText(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        text = (request.text or "").strip() or "你好"
        reply = f"小智收到：{text}"
        logger.info(f"Agent stub client_id={client_id} text={text!r} -> {reply!r}")

        stub = audio_pb2_grpc.AudioSpeakerServiceStub(self.pool.channel(SPEAKER_SERVICE))
        stub.SpeakText(
            audio_pb2.SpeakRequest(
                message_id=request.message_id or getattr(context, "message_id", ""),
                client_id=client_id,
                text=reply,
                emotion="neutral",
                index=1,
                total=1,
            ),
            metadata=self.pool.metadata(client_id, request.message_id),
            timeout=10,
        )
        return audio_pb2.TextResponse(code=0, msg="ok", result=reply)
