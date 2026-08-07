from __future__ import annotations

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc, command_pb2, command_pb2_grpc
from xiaozhi_common.constants import ACCESS_SERVICE, AGENT_SERVICE, RECEIVER_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool


class AudioPreprocessServicer(audio_pb2_grpc.AudioPreprocessServiceServicer):
    """Phase-1 stub: treat chunk as end-of-utterance, ASR -> agent."""

    def __init__(self, pool: GrpcClientPool) -> None:
        self.pool = pool

    def _notify_listen(self, client_id: str, message_id: str, command: str) -> None:
        try:
            stub = command_pb2_grpc.AccessCommandServiceStub(
                self.pool.channel(ACCESS_SERVICE)
            )
            stub.SendCommand(
                command_pb2.CommandRequest(
                    message_id=message_id,
                    client_id=client_id,
                    command=command,
                    payload="",
                ),
                metadata=self.pool.metadata(client_id, message_id),
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"notify access command={command} failed: {exc}")

    def _forward_text(self, client_id: str, message_id: str, text: str) -> str:
        stub = audio_pb2_grpc.AgentServiceStub(self.pool.channel(AGENT_SERVICE))
        resp = stub.SendText(
            audio_pb2.TextRequest(
                message_id=message_id, client_id=client_id, text=text
            ),
            metadata=self.pool.metadata(client_id, message_id),
            timeout=15,
        )
        return resp.result or ""

    def SendAudioChunk(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        message_id = request.message_id or getattr(context, "message_id", "")
        data = request.data or b""
        logger.info(
            f"Preprocess chunk client_id={client_id} bytes={len(data)} format={request.format}"
        )

        self._notify_listen(client_id, message_id, "listen_start")

        asr_stub = audio_pb2_grpc.AudioReceiverServiceStub(
            self.pool.channel(RECEIVER_SERVICE)
        )
        asr = asr_stub.AsrRecognize(
            audio_pb2.AsrRecognizeRequest(
                message_id=message_id,
                client_id=client_id,
                pcm=data,
                sample_rate=16000,
            ),
            metadata=self.pool.metadata(client_id, message_id),
            timeout=10,
        )
        text = asr.text or "你好"
        self._notify_listen(client_id, message_id, "listen_stop")
        result = self._forward_text(client_id, message_id, text)
        return audio_pb2.AudioChunkResponse(code=0, msg="ok", result=result)

    def SendText(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        message_id = request.message_id or getattr(context, "message_id", "")
        text = request.text or ""
        logger.info(f"Preprocess text inject client_id={client_id} text={text!r}")
        result = self._forward_text(client_id, message_id, text)
        return audio_pb2.TextResponse(code=0, msg="ok", result=result)
