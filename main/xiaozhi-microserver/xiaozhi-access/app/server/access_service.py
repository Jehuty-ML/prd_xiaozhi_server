from __future__ import annotations

import json

from loguru import logger
from xiaozhi import (
    admin_pb2,
    admin_pb2_grpc,
    audio_pb2,
    audio_pb2_grpc,
    command_pb2,
    command_pb2_grpc,
    session_pb2,
    session_pb2_grpc,
)
from app.ws.gateway_runtime import gateway_runtime
from app.ws.manager import connection_manager


class AccessAudioServicer(audio_pb2_grpc.AccessAudioServiceServicer):
    def SendTtsAudio(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        audio = request.audio or b""
        text = request.text or ""
        logger.info(
            f"Access TTS downlink client_id={client_id} bytes={len(audio)} text={text!r}"
        )
        ok_bin = connection_manager.send_bytes_threadsafe(client_id, audio)
        meta = connection_manager.get_meta(client_id)
        frame = json.dumps(
            {
                "type": "tts",
                "text": text,
                "index": request.index,
                "total": request.total,
                "format": request.format or "stub",
                "audio_bytes": len(audio),
                "session_id": meta.get("session_id", ""),
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
        # close_after_chat: mark meta; do not push a fake command frame to device
        if command == "close_after_chat":
            connection_manager.set_meta(client_id, "close_after_chat", True)
            logger.info(f"Access close_after_chat client_id={client_id}")
            return command_pb2.CommandResponse(code=0, msg="ok", result=command)
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

    def GetConnectionStats(self, request, context):  # noqa: N802, ANN001
        reg = gateway_runtime.registry
        return command_pb2.ConnectionStatsResponse(
            code=0,
            msg="ok",
            active=reg.active_count,
            max_connections=reg.limits.max_connections,
            max_connections_per_device=reg.limits.max_connections_per_device,
            rejected_total=reg.rejected_total,
        )


class AccessSessionServicer(session_pb2_grpc.SessionServiceServicer):
    def Abort(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        reason = request.reason or "abort"
        connection_manager.set_state(client_id, "aborted")
        meta = connection_manager.get_meta(client_id)
        frame = json.dumps(
            {
                "type": "tts",
                "state": "stop",
                "session_id": meta.get("session_id", ""),
                "reason": reason,
            },
            ensure_ascii=False,
        )
        connection_manager.send_text_threadsafe(client_id, frame)
        logger.info(f"Access abort client_id={client_id} reason={reason}")
        return session_pb2.AbortResponse(code=0, msg="ok", result="aborted")

    def Ping(self, request, context):  # noqa: N802, ANN001
        return session_pb2.SessionPingResponse(code=0, msg="ok", result="pong")


class AccessConfigApplyServicer(admin_pb2_grpc.ConfigApplyServiceServicer):
    def ApplyConfig(self, request, context):  # noqa: N802, ANN001
        reason = request.reason or "rpc"
        try:
            config = json.loads(request.config_json or "{}")
            if not isinstance(config, dict):
                return admin_pb2.ApplyConfigResponse(
                    code=1, msg="invalid config_json", result=""
                )
            gateway_runtime.apply_config(config, reason=reason)
            return admin_pb2.ApplyConfigResponse(
                code=0, msg="ok", result="applied"
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"ApplyConfig failed: {exc}")
            return admin_pb2.ApplyConfigResponse(
                code=1, msg=str(exc), result=""
            )


class AccessDeviceProxyServicer(command_pb2_grpc.AccessDeviceProxyServiceServicer):
    """Agent → device MCP/IoT JSON frames (phase-3)."""

    def SendToDevice(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        text = request.json_text or ""
        if not text:
            return command_pb2.DeviceMessageResponse(
                code=1, msg="empty json_text", result=""
            )
        ok = connection_manager.send_text_threadsafe(client_id, text)
        if not ok:
            return command_pb2.DeviceMessageResponse(
                code=1, msg="client not connected", result=""
            )
        logger.info(
            f"DeviceProxy SendToDevice client_id={client_id} bytes={len(text)}"
        )
        return command_pb2.DeviceMessageResponse(code=0, msg="ok", result="delivered")
