"""WS text protocol dispatch (phase-2 gateway shell)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional

from fastapi import WebSocket
from loguru import logger

from xiaozhi import admin_pb2, admin_pb2_grpc, audio_pb2, audio_pb2_grpc
from xiaozhi_common.constants import (
    AGENT_SERVICE,
    CONTROL_ADMIN_SERVICE,
    PREPROCESS_SERVICE,
    SPEAKER_SERVICE,
)
from xiaozhi_common.grpc.client import GrpcClientPool
from app.ws.gateway_runtime import gateway_runtime
from app.ws.manager import connection_manager


async def dispatch_text(
    *,
    pool: GrpcClientPool,
    websocket: WebSocket,
    client_id: str,
    session_id: str,
    text: str,
) -> None:
    text = (text or "").strip()
    if not text:
        return
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        await _inject_text(pool, client_id, text)
        return

    if not isinstance(payload, dict):
        await _inject_text(pool, client_id, text)
        return

    msg_type = str(payload.get("type") or "").lower()
    if msg_type == "ping":
        if (gateway_runtime.config.get("server") or {}).get(
            "enable_websocket_ping", True
        ):
            await websocket.send_text(json.dumps({"type": "pong"}))
        return
    if msg_type == "hello":
        await _handle_hello(websocket, session_id, payload)
        return
    if msg_type == "listen":
        await _handle_listen(pool, websocket, client_id, session_id, payload)
        return
    if msg_type == "abort":
        await _handle_abort(pool, websocket, client_id, session_id, payload)
        return
    if msg_type == "server":
        await _handle_server(pool, websocket, payload)
        return
    if msg_type in ("iot", "mcp"):
        await _forward_device_event(pool, websocket, client_id, session_id, msg_type, payload)
        return
    if "text" in payload:
        await _inject_text(pool, client_id, str(payload["text"]))
        return
    logger.debug(f"Unhandled WS text type={msg_type!r} client_id={client_id}")


async def _forward_device_event(
    pool: GrpcClientPool,
    websocket: WebSocket,
    client_id: str,
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    message_id = uuid.uuid4().hex

    def _call():
        stub = audio_pb2_grpc.AgentServiceStub(pool.channel(AGENT_SERVICE))
        return stub.HandleDeviceEvent(
            audio_pb2.DeviceEventRequest(
                message_id=message_id,
                client_id=client_id,
                event_type=event_type,
                payload_json=json.dumps(payload, ensure_ascii=False),
            ),
            metadata=pool.metadata(client_id, message_id),
            timeout=15,
        )

    try:
        resp = await asyncio.to_thread(_call)
        await websocket.send_text(
            json.dumps(
                {
                    "type": event_type,
                    "status": "ok" if int(resp.code) == 0 else "error",
                    "message": resp.result or resp.msg,
                    "session_id": session_id,
                },
                ensure_ascii=False,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"DeviceEvent forward failed: {exc}")
        await websocket.send_text(
            json.dumps(
                {
                    "type": event_type,
                    "status": "error",
                    "message": str(exc),
                    "session_id": session_id,
                },
                ensure_ascii=False,
            )
        )


async def _handle_hello(
    websocket: WebSocket, session_id: str, payload: dict[str, Any]
) -> None:
    welcome = gateway_runtime.welcome_message(session_id)
    audio_params = payload.get("audio_params")
    if isinstance(audio_params, dict):
        welcome["audio_params"] = audio_params
    features = payload.get("features")
    if isinstance(features, dict):
        welcome["features"] = features
        client_id = connection_manager.get_client_id(websocket) or ""
        if client_id:
            aec = bool(features.get("aec"))
            connection_manager.set_meta(client_id, "aec_enabled", aec)
            if "mcp" in features:
                connection_manager.set_meta(client_id, "mcp", bool(features.get("mcp")))
    await websocket.send_text(json.dumps(welcome, ensure_ascii=False))


async def _handle_listen(
    pool: GrpcClientPool,
    websocket: WebSocket,
    client_id: str,
    session_id: str,
    payload: dict[str, Any],
) -> None:
    state = str(payload.get("state") or "").lower()
    mode = payload.get("mode")
    if mode:
        connection_manager.set_meta(client_id, "listen_mode", str(mode))

    meta = connection_manager.get_meta(client_id)
    listen_mode = str(meta.get("listen_mode") or mode or "auto")
    aec_enabled = bool(meta.get("aec_enabled"))

    if state == "start":
        current = str(connection_manager.get_state(client_id) or "").upper()
        if current in ("SPEAKING", "THINKING"):
            # Cancel LLM/TTS before listen; else SpeakText hits LISTENING gate.
            connection_manager.set_state(client_id, "abort")
            await _abort_peers(pool, client_id, reason="listen_barge_in")
        connection_manager.set_state(client_id, "listen_start")
        await _control_listen(
            pool,
            client_id,
            state="start",
            mode=listen_mode,
            aec_enabled=aec_enabled,
        )
        return
    if state == "stop":
        connection_manager.set_state(client_id, "listen_stop")
        await _control_listen(
            pool,
            client_id,
            state="stop",
            mode=listen_mode,
            aec_enabled=aec_enabled,
        )
        return
    if state == "detect":
        current = str(connection_manager.get_state(client_id) or "").upper()
        if current in ("SPEAKING", "THINKING"):
            connection_manager.set_state(client_id, "abort")
            await _abort_peers(pool, client_id, reason="detect_barge_in")
        connection_manager.set_state(client_id, "detect")
        content = payload.get("text") or payload.get("data") or ""
        if content:
            # Detect inject: ControlListen(detect) or legacy SendText path
            await _control_listen(
                pool,
                client_id,
                state="detect",
                mode=listen_mode,
                text=str(content),
                aec_enabled=aec_enabled,
            )
        return

    # Backward-compatible smoke: {"type":"listen","text":"..."} without state
    content = payload.get("text") or payload.get("data") or ""
    if content:
        await _inject_text(pool, client_id, str(content))


async def _control_listen(
    pool: GrpcClientPool,
    client_id: str,
    *,
    state: str,
    mode: str = "auto",
    text: str = "",
    aec_enabled: bool = False,
) -> None:
    message_id = uuid.uuid4().hex

    def _call():
        stub = audio_pb2_grpc.AudioPreprocessServiceStub(
            pool.channel(PREPROCESS_SERVICE)
        )
        return stub.ControlListen(
            audio_pb2.ListenControlRequest(
                message_id=message_id,
                client_id=client_id,
                state=state,
                mode=mode,
                text=text,
                aec_enabled=aec_enabled,
            ),
            metadata=pool.metadata(client_id, message_id),
            timeout=30,
        )

    try:
        resp = await asyncio.to_thread(_call)
        logger.info(
            f"ControlListen done client={client_id} state={state} result={resp.result!r}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"ControlListen failed: {exc}")
        # Fallback for detect: keep smoke path via SendText
        if state == "detect" and text:
            await _inject_text(pool, client_id, text)

async def _abort_peers(
    pool: GrpcClientPool,
    client_id: str,
    *,
    reason: str = "barge_in",
) -> None:
    """Fan-out abort to agent + speaker + preprocess (cancel in-flight turn)."""
    message_id = uuid.uuid4().hex

    def _abort_agent():
        stub = audio_pb2_grpc.AgentServiceStub(pool.channel(AGENT_SERVICE))
        return stub.Abort(
            audio_pb2.AgentAbortRequest(
                message_id=message_id, client_id=client_id, reason=str(reason)
            ),
            metadata=pool.metadata(client_id, message_id),
            timeout=5,
        )

    def _abort_speaker():
        stub = audio_pb2_grpc.AudioSpeakerServiceStub(pool.channel(SPEAKER_SERVICE))
        return stub.Abort(
            audio_pb2.SpeakerAbortRequest(
                message_id=message_id, client_id=client_id, reason=str(reason)
            ),
            metadata=pool.metadata(client_id, message_id),
            timeout=5,
        )

    def _abort_preprocess():
        stub = audio_pb2_grpc.AudioPreprocessServiceStub(
            pool.channel(PREPROCESS_SERVICE)
        )
        return stub.Abort(
            audio_pb2.PreprocessAbortRequest(
                message_id=message_id, client_id=client_id, reason=str(reason)
            ),
            metadata=pool.metadata(client_id, message_id),
            timeout=5,
        )

    try:
        await asyncio.to_thread(_abort_agent)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Agent abort fan-out skipped: {exc}")
    try:
        await asyncio.to_thread(_abort_speaker)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Speaker abort fan-out skipped: {exc}")
    try:
        await asyncio.to_thread(_abort_preprocess)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Preprocess abort fan-out skipped: {exc}")


async def _handle_abort(
    pool: GrpcClientPool,
    websocket: WebSocket,
    client_id: str,
    session_id: str,
    payload: dict[str, Any],
) -> None:
    reason = payload.get("reason") or "client_abort"
    connection_manager.set_state(client_id, "abort")
    await websocket.send_text(
        json.dumps(
            {
                "type": "tts",
                "state": "stop",
                "session_id": session_id,
                "reason": reason,
            },
            ensure_ascii=False,
        )
    )
    await _abort_peers(pool, client_id, reason=str(reason))
    logger.info(f"WS abort client_id={client_id} session={session_id} reason={reason}")


async def _handle_server(
    pool: GrpcClientPool, websocket: WebSocket, payload: dict[str, Any]
) -> None:
    action = str(payload.get("action") or "")
    content = payload.get("content") or {}
    if not isinstance(content, dict):
        content = {}

    if action == "update_config":
        secret = str(content.get("secret") or "")
        expected = gateway_runtime.manager_secret()
        if not expected or secret != expected:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "server",
                        "status": "error",
                        "message": "服务器密钥验证失败",
                        "content": {"action": "update_config"},
                    },
                    ensure_ascii=False,
                )
            )
            return
        ok = await _reload_via_admin(pool, reason="ws_server_update_config")
        await websocket.send_text(
            json.dumps(
                {
                    "type": "server",
                    "status": "success" if ok else "error",
                    "message": "配置更新成功" if ok else "更新服务器配置失败",
                    "content": {"action": "update_config"},
                },
                ensure_ascii=False,
            )
        )
        return

    if action == "broadcast_speak":
        secret = str(content.get("secret") or "")
        expected = gateway_runtime.manager_secret()
        # Fail closed like update_config: empty expected secret must not allow
        # unauthenticated fleet-wide TTS interrupt.
        if not expected or secret != expected:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "server",
                        "status": "error",
                        "message": "服务器密钥验证失败",
                        "content": {"action": "broadcast_speak"},
                    },
                    ensure_ascii=False,
                )
            )
            return
        text = str(content.get("text") or payload.get("text") or "").strip()
        if not text:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "server",
                        "status": "error",
                        "message": "broadcast_speak missing text",
                        "content": {"action": "broadcast_speak"},
                    },
                    ensure_ascii=False,
                )
            )
            return
        exclude = connection_manager.get_client_id(websocket) or ""
        spoken = await _broadcast_speak(pool, text, exclude_client_id=exclude)
        await websocket.send_text(
            json.dumps(
                {
                    "type": "server",
                    "status": "success",
                    "message": f"broadcast_speak ok spoken={spoken}",
                    "content": {"action": "broadcast_speak", "spoken": spoken},
                },
                ensure_ascii=False,
            )
        )
        return

    await websocket.send_text(
        json.dumps(
            {
                "type": "server",
                "status": "error",
                "message": f"unknown action: {action}",
            },
            ensure_ascii=False,
        )
    )


async def _broadcast_speak(
    pool: GrpcClientPool, text: str, *, exclude_client_id: str = ""
) -> int:
    message_id = uuid.uuid4().hex

    def _call():
        stub = audio_pb2_grpc.AudioSpeakerServiceStub(pool.channel(SPEAKER_SERVICE))
        return stub.BroadcastSpeak(
            audio_pb2.BroadcastSpeakRequest(
                message_id=message_id,
                text=text,
                exclude_client_id=exclude_client_id,
            ),
            metadata=pool.metadata(message_id=message_id),
            timeout=60,
        )

    try:
        resp = await asyncio.to_thread(_call)
        return int(resp.spoken or 0)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"BroadcastSpeak failed: {exc}")
        return 0


async def _reload_via_admin(pool: GrpcClientPool, reason: str) -> bool:
    message_id = uuid.uuid4().hex

    def _call():
        stub = admin_pb2_grpc.ModelAdminServiceStub(pool.channel(CONTROL_ADMIN_SERVICE))
        return stub.ReloadConfig(
            admin_pb2.ReloadConfigRequest(message_id=message_id, reason=reason),
            metadata=pool.metadata(message_id=message_id),
            timeout=30,
        )

    try:
        resp = await asyncio.to_thread(_call)
        return int(resp.code) == 0
    except Exception as exc:  # noqa: BLE001
        logger.error(f"ReloadConfig via admin failed: {exc}")
        return False


async def _inject_text(pool: GrpcClientPool, client_id: str, content: str) -> None:
    message_id = uuid.uuid4().hex
    stub = audio_pb2_grpc.AudioPreprocessServiceStub(pool.channel(PREPROCESS_SERVICE))

    def _call():
        return stub.SendText(
            audio_pb2.TextRequest(
                message_id=message_id, client_id=client_id, text=content
            ),
            metadata=pool.metadata(client_id, message_id),
            timeout=30,
        )

    resp = await asyncio.to_thread(_call)
    logger.info(f"Text inject done client_id={client_id} result={resp.result!r}")
