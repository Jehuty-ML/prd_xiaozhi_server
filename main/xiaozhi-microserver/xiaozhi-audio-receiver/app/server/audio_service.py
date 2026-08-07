from __future__ import annotations

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc

from app.providers.asr.base import ASRProviderBase


class AudioReceiverServicer(audio_pb2_grpc.AudioReceiverServiceServicer):
    """Phase-5 ASR: provider-backed PCM → text."""

    def __init__(self, asr: ASRProviderBase) -> None:
        self.asr = asr

    def AsrRecognize(self, request, context):  # noqa: N802, ANN001
        client_id = getattr(context, "client_id", "") or request.client_id
        pcm = request.pcm or b""
        sample_rate = int(request.sample_rate or 16000)
        try:
            text = self.asr.recognize(pcm, sample_rate=sample_rate) or ""
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"ASR recognize failed client={client_id}: {exc}")
            return audio_pb2.AsrRecognizeResponse(
                code=1, msg=str(exc), text=""
            )
        logger.info(
            f"ASR client_id={client_id} pcm_bytes={len(pcm)} -> text={text!r}"
        )
        return audio_pb2.AsrRecognizeResponse(code=0, msg="ok", text=text)
