from __future__ import annotations

import uuid

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc, command_pb2, command_pb2_grpc
from xiaozhi_common.constants import (
    ACCESS_SERVICE,
    AGENT_SERVICE,
    PREPROCESS_SERVICE,
)
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.session import gate_start_play, send_state_command

from app.core.speak_session import session_store


class AudioSpeakerServicer(audio_pb2_grpc.AudioSpeakerServiceServicer):
    """Phase-4 TTS: queue + synthesize + rate-controlled downlink."""

    def __init__(self, pool: GrpcClientPool) -> None:
        self.pool = pool

    def SpeakText(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        if not client_id:
            return audio_pb2.SpeakResponse(code=1, msg="missing client_id", result="")
        message_id = request.message_id or uuid.uuid4().hex
        text = request.text or ""
        end = bool(request.end)
        index = int(request.index or 0)
        total = int(request.total or 0)
        logger.info(
            f"SpeakText client={client_id} idx={index} end={end} text={text[:60]!r}"
        )
        if end:
            send_state_command(self.pool, client_id, "tts_end", message_id=message_id)
        else:
            # micro_service-style: only THINKING/IDLE/SPEAKING may start play
            if not gate_start_play(self.pool, client_id, message_id=message_id):
                return audio_pb2.SpeakResponse(
                    code=2, msg="illegal_state", result="rejected"
                )
            send_state_command(
                self.pool, client_id, "tts_start", message_id=message_id
            )
        ok = session_store.speak_text(
            client_id,
            text,
            message_id=message_id,
            index=index or 1,
            total=total,
            end=end,
            emotion=request.emotion or "neutral",
        )
        if not ok:
            return audio_pb2.SpeakResponse(
                code=2, msg="blocked_by_broadcast", result="rejected"
            )
        return audio_pb2.SpeakResponse(
            code=0, msg="ok", result="ended" if end else "queued"
        )

    def Abort(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        reason = request.reason or "abort"
        ok = session_store.abort(client_id, reason) if client_id else False
        logger.info(f"Speaker Abort client={client_id} reason={reason} ok={ok}")
        return audio_pb2.SpeakerAbortResponse(
            code=0, msg="ok", result="aborted" if ok else "no_session"
        )

    def BroadcastSpeak(self, request, context):  # noqa: N802, ANN001
        text = (request.text or "").strip()
        if not text:
            return audio_pb2.BroadcastSpeakResponse(
                code=1, msg="empty text", result="", spoken=0
            )
        exclude = request.exclude_client_id or ""
        message_id = request.message_id or uuid.uuid4().hex
        clients = self._list_clients(message_id)
        spoken = 0
        for client_id in clients:
            if exclude and client_id == exclude:
                continue
            # Stop in-flight dialogue / detect before owning the audio line.
            self._abort_peer_dialogue(client_id, message_id)
            session_store.abort(client_id, "broadcast")
            session_store.speak_text(
                client_id,
                text,
                message_id=message_id,
                index=1,
                total=1,
                end=False,
                is_broadcast=True,
            )
            session_store.speak_text(
                client_id,
                "",
                message_id=message_id,
                index=1,
                total=1,
                end=True,
                is_broadcast=True,
            )
            spoken += 1
        logger.info(
            f"BroadcastSpeak spoken={spoken} exclude={exclude!r} text={text[:40]!r}"
        )
        return audio_pb2.BroadcastSpeakResponse(
            code=0, msg="ok", result="broadcast", spoken=spoken
        )

    def _abort_peer_dialogue(self, client_id: str, message_id: str) -> None:
        """Abort agent chat and preprocess listen so they cannot steal the line."""
        reason = "broadcast"
        try:
            stub = audio_pb2_grpc.AgentServiceStub(self.pool.channel(AGENT_SERVICE))
            stub.Abort(
                audio_pb2.AgentAbortRequest(
                    message_id=message_id,
                    client_id=client_id,
                    reason=reason,
                ),
                metadata=self.pool.metadata(message_id=message_id),
                timeout=3,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"BroadcastSpeak agent abort failed client={client_id}: {exc}")
        try:
            stub = audio_pb2_grpc.AudioPreprocessServiceStub(
                self.pool.channel(PREPROCESS_SERVICE)
            )
            stub.Abort(
                audio_pb2.PreprocessAbortRequest(
                    message_id=message_id,
                    client_id=client_id,
                    reason=reason,
                ),
                metadata=self.pool.metadata(message_id=message_id),
                timeout=3,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"BroadcastSpeak preprocess abort failed client={client_id}: {exc}"
            )

    def _list_clients(self, message_id: str) -> list[str]:
        try:
            stub = command_pb2_grpc.AccessCommandServiceStub(
                self.pool.channel(ACCESS_SERVICE)
            )
            resp = stub.ListClients(
                command_pb2.ListClientsRequest(message_id=message_id),
                metadata=self.pool.metadata(message_id=message_id),
                timeout=5,
            )
            return list(resp.client_ids or [])
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ListClients failed: {exc}")
            return []
