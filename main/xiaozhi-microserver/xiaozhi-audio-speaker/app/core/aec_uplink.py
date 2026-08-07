"""gRPC helper: speaker → preprocess PushAecReference."""

from __future__ import annotations

import time
from typing import Optional

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc
from xiaozhi_common.constants import PREPROCESS_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool

try:
    from opuslib_next import Decoder

    _HAS_OPUS = True
except Exception:  # noqa: BLE001
    Decoder = None  # type: ignore
    _HAS_OPUS = False


class AecReferencePusher:
    """Decode TTS Opus frames to PCM and push to preprocess for AEC."""

    def __init__(self, pool: GrpcClientPool) -> None:
        self.pool = pool
        self._decoders: dict[str, object] = {}

    def _decoder(self, client_id: str):
        if not _HAS_OPUS:
            return None
        dec = self._decoders.get(client_id)
        if dec is None:
            dec = Decoder(16000, 1)
            self._decoders[client_id] = dec
        return dec

    def push_opus_frame(
        self,
        *,
        client_id: str,
        opus_frame: bytes,
        message_id: str = "",
        timestamp: int = 0,
    ) -> bool:
        if not opus_frame:
            return False
        pcm = b""
        dec = self._decoder(client_id)
        if dec is not None:
            try:
                pcm = dec.decode(opus_frame, 960)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"AEC opus decode failed: {exc}")
                return False
        else:
            # No opuslib — skip reference (AEC disabled effectively)
            return False
        ts = int(timestamp) if timestamp else int(time.time() * 1000)
        try:
            stub = audio_pb2_grpc.AudioPreprocessServiceStub(
                self.pool.channel(PREPROCESS_SERVICE)
            )
            resp = stub.PushAecReference(
                audio_pb2.AecReferenceRequest(
                    message_id=message_id,
                    client_id=client_id,
                    timestamp=ts,
                    pcm=pcm,
                    format="pcm",
                ),
                metadata=self.pool.metadata(client_id, message_id),
                timeout=5,
            )
            return int(resp.code) == 0
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"PushAecReference failed client={client_id}: {exc}")
            return False

    def clear_decoder(self, client_id: str) -> None:
        self._decoders.pop(client_id, None)
