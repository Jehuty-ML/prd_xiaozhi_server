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
from app.ws.session_fsm import device_session_store
from xiaozhi_common.session import SessionEvent, resolve_session_mode


class AccessAudioServicer(audio_pb2_grpc.AccessAudioServiceServicer):
    def SendTtsAudio(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        audio = request.audio or b""
        text = request.text or ""
        state = (request.state or "").strip()
        meta = connection_manager.get_meta(client_id)
        session_id = request.session_id or meta.get("session_id", "")
        ok_bin = False
        ok_txt = False

        # Device-compatible TTS state frames (start / sentence_start / stop)
        if state:
            frame: dict = {
                "type": "tts",
                "state": state,
                "session_id": session_id,
            }
            if text:
                frame["text"] = text
            if request.index:
                frame["index"] = request.index
            if request.total:
                frame["total"] = request.total
            ok_txt = connection_manager.send_text_threadsafe(
                client_id, json.dumps(frame, ensure_ascii=False)
            )
            if state in ("start", "sentence_start"):
                connection_manager.transition(
                    client_id, SessionEvent.TTS_START, detail=f"tts_{state}"
                )
            elif state == "stop":
                connection_manager.transition(
                    client_id, SessionEvent.TTS_END, detail="tts_stop"
                )
            logger.info(
                f"Access TTS state={state} client_id={client_id} text={text!r}"
            )

        # Binary Opus (or other) frames
        if audio:
            ok_bin = connection_manager.send_bytes_threadsafe(client_id, audio)
            logger.debug(
                f"Access TTS audio client_id={client_id} bytes={len(audio)} "
                f"format={request.format or 'opus'}"
            )
            # Track speaking for barge-in alignment
            if state != "stop":
                connection_manager.set_meta(client_id, "speaking", True)
            else:
                connection_manager.set_meta(client_id, "speaking", False)

        if state == "stop":
            connection_manager.set_meta(client_id, "speaking", False)

        if not (ok_bin or ok_txt):
            return audio_pb2.TtsAudioResponse(
                code=1, msg="client not connected", result=""
            )
        return audio_pb2.TtsAudioResponse(code=0, msg="ok", result="delivered")


class AccessCommandServicer(command_pb2_grpc.AccessCommandServiceServicer):
    def SendCommand(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        command = request.command or ""
        # Map preprocess / peer notifications onto validated FSM events
        fsm_commands = {
            "listen_start",
            "listen_stop",
            "idle",
            "speaking",
            "speak_start",
            "speak_stop",
            "tts_start",
            "tts_end",
            "detect",
            "chat_start",
            "think",
            "voice_end",
            "abort",
            "reset",
            "close_after_chat",
        }
        if command in fsm_commands or command == "close_after_chat":
            if command == "close_after_chat":
                connection_manager.set_meta(client_id, "close_after_chat", True)
                logger.info(
                    f"Access state command client_id={client_id} command={command}"
                )
                return command_pb2.CommandResponse(code=0, msg="ok", result=command)
            ok, state = device_session_store.apply_command(
                client_id, command, detail="SendCommand"
            )
            connection_manager.set_meta(client_id, "fsm_state", state)
            if not ok:
                logger.warning(
                    f"Access FSM reject client_id={client_id} command={command} "
                    f"state={state}"
                )
                return command_pb2.CommandResponse(
                    code=2, msg="illegal_transition", result=state
                )
            logger.info(
                f"Access state command client_id={client_id} command={command} "
                f"state={state}"
            )
            return command_pb2.CommandResponse(code=0, msg="ok", result=state)
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

    def ListClients(self, request, context):  # noqa: N802, ANN001
        return command_pb2.ListClientsResponse(
            code=0,
            msg="ok",
            client_ids=connection_manager.list_client_ids(),
        )


class AccessSessionServicer(session_pb2_grpc.SessionServiceServicer):
    def Abort(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        reason = request.reason or "abort"
        connection_manager.transition(
            client_id, SessionEvent.ABORT, detail=reason
        )
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
            old_mode = resolve_session_mode(gateway_runtime.config)
            gateway_runtime.apply_config(config, reason=reason)
            new_mode = resolve_session_mode(config)
            device_session_store.set_default_mode(new_mode)
            # Only barge-in online devices when session_state.mode actually changes
            if new_mode != old_mode:
                n = device_session_store.apply_mode_to_all(
                    new_mode, only_if_changed=True
                )
                logger.info(
                    f"session_state.mode {old_mode} -> {new_mode}, "
                    f"switched={n} reason={reason}"
                )
            else:
                logger.info(
                    f"session_state.mode unchanged ({new_mode}), skip interrupt"
                )
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
