from __future__ import annotations

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc


class AudioReceiverServicer(audio_pb2_grpc.AudioReceiverServiceServicer):
    """Phase-1 stub ASR: always returns a fixed transcript."""

    def AsrRecognize(self, request, context):  # noqa: N802, ANN001
        client_id = getattr(context, "client_id", "") or request.client_id
        pcm_len = len(request.pcm or b"")
        text = "你好"
        logger.info(
            f"ASR stub client_id={client_id} pcm_bytes={pcm_len} -> text={text!r}"
        )
        return audio_pb2.AsrRecognizeResponse(code=0, msg="ok", text=text)
