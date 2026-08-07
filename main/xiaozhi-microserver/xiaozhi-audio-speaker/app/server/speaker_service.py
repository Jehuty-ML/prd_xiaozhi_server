from __future__ import annotations

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc
from xiaozhi_common.constants import ACCESS_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool


class AudioSpeakerServicer(audio_pb2_grpc.AudioSpeakerServiceServicer):
    """Phase-1 stub TTS: emit placeholder bytes and push to access."""

    def __init__(self, pool: GrpcClientPool) -> None:
        self.pool = pool

    def SpeakText(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        text = request.text or ""
        # Fake PCM-ish payload for pipeline verification (UTF-8 marker + zeros)
        audio = b"XIAOZHI_TTS_STUB:" + text.encode("utf-8")
        logger.info(
            f"TTS stub client_id={client_id} text={text!r} audio_bytes={len(audio)}"
        )

        stub = audio_pb2_grpc.AccessAudioServiceStub(self.pool.channel(ACCESS_SERVICE))
        stub.SendTtsAudio(
            audio_pb2.TtsAudioRequest(
                message_id=request.message_id or getattr(context, "message_id", ""),
                client_id=client_id,
                audio=audio,
                text=text,
                index=request.index or 1,
                total=request.total or 1,
                format="stub",
            ),
            metadata=self.pool.metadata(client_id, request.message_id),
            timeout=10,
        )
        return audio_pb2.SpeakResponse(code=0, msg="ok", result="spoken")
