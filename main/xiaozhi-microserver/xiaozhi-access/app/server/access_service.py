from __future__ import annotations

import json

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc, command_pb2, command_pb2_grpc, session_pb2, session_pb2_grpc
from app.ws.manager import connection_manager


class AccessAudioServicer(audio_pb2_grpc.AccessAudioServiceServicer):
    def SendTtsAudio(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        audio = request.audio or b""
        text = request.text or ""
        logger.info(
            f"Access TTS downlink client_id={client_id} bytes={len(audio)} text={text!r}"
        )
        # Push binary audio
        ok_bin = connection_manager.send_bytes_threadsafe(client_id, audio)
        # Also push a JSON control frame for easy smoke testing
        frame = json.dumps(
            {
                "type": "tts",
                "text": text,
                "index": request.index,
                "total": request.total,
                "format": request.format or "stub",
                "audio_bytes": len(audio),
            },
            ensure_ascii=False,
        )
        ok_txt = connection_manager.send_text_threadsafe(client_id, frame)
        if not (ok_bin or ok_txt):
            return audio_pb2.TtsAudioResponse(
                code=1, msg="client not connected", result=""
            )
        return audio_pb2.TtsAudioResponse(code=0, msg="ok", result="delivered")


class AccessCommandServicer(command_pb2_grpc.AccessCommandServiceServicer):
    def SendCommand(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        command = request.command or ""
        connection_manager.set_state(client_id, command)
        frame = json.dumps(
            {"type": "command", "command": command, "payload": request.payload or ""},
            ensure_ascii=False,
        )
        connection_manager.send_text_threadsafe(client_id, frame)
        logger.info(f"Access command client_id={client_id} command={command}")
        return command_pb2.CommandResponse(code=0, msg="ok", result=command)

    def GetDeviceState(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        state = connection_manager.get_state(client_id)
        return command_pb2.DeviceStateResponse(code=0, msg="ok", state=state)


class AccessSessionServicer(session_pb2_grpc.SessionServiceServicer):
    def Abort(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        reason = request.reason or "abort"
        connection_manager.set_state(client_id, "aborted")
        frame = json.dumps(
            {"type": "abort", "reason": reason},
            ensure_ascii=False,
        )
        connection_manager.send_text_threadsafe(client_id, frame)
        logger.info(f"Access abort client_id={client_id} reason={reason}")
        return session_pb2.AbortResponse(code=0, msg="ok", result="aborted")

    def Ping(self, request, context):  # noqa: N802, ANN001
        return session_pb2.SessionPingResponse(code=0, msg="ok", result="pong")
