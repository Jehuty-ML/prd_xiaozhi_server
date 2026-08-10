from __future__ import annotations

import uuid

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc, command_pb2, command_pb2_grpc
from xiaozhi_common.constants import (
    ACCESS_SERVICE,
    AGENT_SERVICE,
    RECEIVER_SERVICE,
    SPEAKER_SERVICE,
)
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.resilience import UpstreamError, get_fallback_text
from xiaozhi_common.session import gate_start_listen

from app.core.audio_codec import OpusDecoderSession
from app.core.listen_session import session_store


class AudioPreprocessServicer(audio_pb2_grpc.AudioPreprocessServiceServicer):
    """Phase-5/6: VAD session + ASR flush + AEC reference + listen control + resilience."""

    def __init__(self, pool: GrpcClientPool) -> None:
        self.pool = pool

    # ---- outbound helpers ----

    def _notify_listen(self, client_id: str, message_id: str, command: str) -> None:
        from xiaozhi_common.session import send_state_command

        if command == "listen_start":
            if not gate_start_listen(self.pool, client_id, message_id=message_id):
                # SPEAKING/THINKING — abort peers + FSM, then retry listen.
                logger.info(
                    f"preprocess listen_start denied, barge-in abort client={client_id}"
                )
                self._abort_peers(client_id, message_id, "barge_in")
                send_state_command(
                    self.pool, client_id, "abort", message_id=message_id
                )
                if not gate_start_listen(
                    self.pool, client_id, message_id=message_id
                ):
                    logger.info(
                        f"preprocess skip listen_start gate deny client={client_id}"
                    )
                    return
        try:
            stub = command_pb2_grpc.AccessCommandServiceStub(
                self.pool.channel(ACCESS_SERVICE)
            )
            timeout = self.pool.default_timeout("grpc")
            self.pool.call(
                "access",
                lambda: stub.SendCommand(
                    command_pb2.CommandRequest(
                        message_id=message_id,
                        client_id=client_id,
                        command=command,
                        payload="",
                    ),
                    metadata=self.pool.metadata(client_id, message_id),
                    timeout=min(5.0, timeout),
                ),
                provider="SendCommand",
                use_circuit=False,
                max_retries=0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"notify access command={command} failed: {exc}")

    def _send_to_device(self, client_id: str, message_id: str, json_text: str) -> None:
        try:
            stub = command_pb2_grpc.AccessDeviceProxyServiceStub(
                self.pool.channel(ACCESS_SERVICE)
            )
            timeout = self.pool.default_timeout("grpc")
            self.pool.call(
                "access",
                lambda: stub.SendToDevice(
                    command_pb2.DeviceMessageRequest(
                        message_id=message_id,
                        client_id=client_id,
                        json_text=json_text,
                    ),
                    metadata=self.pool.metadata(client_id, message_id),
                    timeout=min(5.0, timeout),
                ),
                provider="SendToDevice",
                use_circuit=False,
                max_retries=0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"SendToDevice failed: {exc}")

    def _speak_text(self, client_id: str, message_id: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        try:
            stub = audio_pb2_grpc.AudioSpeakerServiceStub(
                self.pool.channel(SPEAKER_SERVICE)
            )
            stub.SpeakText(
                audio_pb2.SpeakRequest(
                    message_id=message_id or uuid.uuid4().hex,
                    client_id=client_id,
                    text=text,
                    emotion="neutral",
                    index=1,
                    total=1,
                    end=True,
                ),
                metadata=self.pool.metadata(client_id, message_id),
                timeout=min(30.0, self.pool.default_timeout("tts")),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"fallback SpeakText failed: {exc}")

    def _forward_text(self, client_id: str, message_id: str, text: str) -> str:
        stub = audio_pb2_grpc.AgentServiceStub(self.pool.channel(AGENT_SERVICE))
        timeout = self.pool.default_timeout("llm")
        try:
            resp = self.pool.call(
                "llm",
                lambda: stub.SendText(
                    audio_pb2.TextRequest(
                        message_id=message_id, client_id=client_id, text=text
                    ),
                    metadata=self.pool.metadata(client_id, message_id),
                    timeout=timeout,
                ),
                provider="AgentSendText",
            )
            return resp.result or ""
        except UpstreamError as exc:
            logger.warning(f"agent SendText upstream failed: {exc}")
            fallback = get_fallback_text(self.pool.config, "llm", exc.kind)
            # Agent hang/timeout never reaches Session.speak — say it here.
            self._speak_text(client_id, message_id, fallback)
            return fallback
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"agent SendText failed: {exc}")
            fallback = get_fallback_text(self.pool.config, "llm")
            self._speak_text(client_id, message_id, fallback)
            return fallback

    def _asr_recognize(self, client_id: str, message_id: str, pcm: bytes) -> str:
        stub = audio_pb2_grpc.AudioReceiverServiceStub(
            self.pool.channel(RECEIVER_SERVICE)
        )
        timeout = self.pool.default_timeout("asr")
        try:
            resp = self.pool.call(
                "asr",
                lambda: stub.AsrRecognize(
                    audio_pb2.AsrRecognizeRequest(
                        message_id=message_id,
                        client_id=client_id,
                        pcm=pcm,
                        sample_rate=16000,
                    ),
                    metadata=self.pool.metadata(client_id, message_id),
                    timeout=timeout,
                ),
                provider="AsrRecognize",
            )
            return resp.text or ""
        except UpstreamError as exc:
            logger.warning(f"ASR upstream failed: {exc}")
            return ""

    def _abort_peers(self, client_id: str, message_id: str, reason: str) -> None:
        try:
            stub = audio_pb2_grpc.AgentServiceStub(self.pool.channel(AGENT_SERVICE))
            stub.Abort(
                audio_pb2.AgentAbortRequest(
                    message_id=message_id, client_id=client_id, reason=reason
                ),
                metadata=self.pool.metadata(client_id, message_id),
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"barge-in agent abort skipped: {exc}")
        try:
            stub = audio_pb2_grpc.AudioSpeakerServiceStub(
                self.pool.channel(SPEAKER_SERVICE)
            )
            stub.Abort(
                audio_pb2.SpeakerAbortRequest(
                    message_id=message_id, client_id=client_id, reason=reason
                ),
                metadata=self.pool.metadata(client_id, message_id),
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"barge-in speaker abort skipped: {exc}")

    # ---- RPCs ----

    def SendAudioChunk(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        message_id = request.message_id or getattr(context, "message_id", "") or uuid.uuid4().hex
        data = request.data or b""
        # proto3 bool has no presence: only force-enable when True; False leaves session as-is
        aec_arg = True if request.aec_enabled else None
        logger.debug(
            f"Preprocess chunk client={client_id} bytes={len(data)} "
            f"fmt={request.format} ts={request.timestamp}"
        )
        try:
            result = session_store.ingest_audio(
                client_id,
                data,
                format=request.format or "opus",
                timestamp=int(request.timestamp or 0),
                aec_enabled=aec_arg,
                listen_mode=request.listen_mode or "",
                message_id=message_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"ingest_audio failed: {exc}")
            return audio_pb2.AudioChunkResponse(code=1, msg=str(exc), result="")
        return audio_pb2.AudioChunkResponse(code=0, msg="ok", result=result or "")

    def SendText(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        message_id = request.message_id or getattr(context, "message_id", "") or uuid.uuid4().hex
        text = request.text or ""
        logger.info(f"Preprocess text inject client_id={client_id} text={text!r}")
        result = self._forward_text(client_id, message_id, text)
        return audio_pb2.TextResponse(code=0, msg="ok", result=result)

    def ControlListen(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        message_id = request.message_id or uuid.uuid4().hex
        result = session_store.control_listen(
            client_id,
            state=request.state or "",
            mode=request.mode or "",
            text=request.text or "",
            aec_enabled=bool(request.aec_enabled),
            message_id=message_id,
        )
        logger.info(
            f"ControlListen client={client_id} state={request.state} "
            f"mode={request.mode} -> {result!r}"
        )
        return audio_pb2.ListenControlResponse(code=0, msg="ok", result=result)

    def PushAecReference(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        pcm = request.pcm or b""
        fmt = (request.format or "pcm").lower()
        if fmt == "opus" and pcm:
            dec = OpusDecoderSession()
            pcm = dec.decode(pcm)
        session_store.push_aec_reference(
            client_id, pcm, timestamp=int(request.timestamp or 0)
        )
        session_store.set_speaking(client_id, True)
        return audio_pb2.AecReferenceResponse(code=0, msg="ok", result="cached")

    def Abort(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        reason = request.reason or "abort"
        if reason in ("disconnect", "unbind", "shutdown"):
            ok = session_store.remove(client_id) if client_id else False
        else:
            ok = session_store.abort(client_id, reason) if client_id else False
        logger.info(f"Preprocess Abort client={client_id} reason={reason} ok={ok}")
        return audio_pb2.PreprocessAbortResponse(
            code=0, msg="ok", result="aborted" if ok else "no_session"
        )
