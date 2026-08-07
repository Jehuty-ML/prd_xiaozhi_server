"""WS text protocol dispatch (phase-2 gateway shell)."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional

from fastapi import WebSocket
from loguru import logger

from xiaozhi import admin_pb2, admin_pb2_grpc, audio_pb2, audio_pb2_grpc
from xiaozhi_common.constants import MODEL_ADMIN_SERVICE, PREPROCESS_SERVICE
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
        await _handle_abort(websocket, client_id, session_id, payload)
        return
    if msg_type == "server":
        await _handle_server(pool, websocket, payload)
        return
    if msg_type in ("iot", "mcp"):
        # Phase 3: proxy via agent; ack for protocol compatibility.
        await websocket.send_text(
            json.dumps(
                {
                    "type": msg_type,
                    "status": "accepted",
                    "message": "queued for phase-3 agent proxy",
                    "session_id": session_id,
                },
                ensure_ascii=False,
            )
        )
        return
    if "text" in payload:
        await _inject_text(pool, client_id, str(payload["text"]))
        return
    logger.debug(f"Unhandled WS text type={msg_type!r} client_id={client_id}")


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

    if state == "start":
        connection_manager.set_state(client_id, "listening")
        return
    if state == "stop":
        connection_manager.set_state(client_id, "idle")
        return
    if state == "detect":
        connection_manager.set_state(client_id, "detect")
        content = payload.get("text") or payload.get("data") or ""
        if content:
            # Keep phase-1 smoke path: inject text through preprocess stub pipeline.
            await _inject_text(pool, client_id, str(content))
        return

    # Backward-compatible smoke: {"type":"listen","text":"..."} without state
    content = payload.get("text") or payload.get("data") or ""
    if content:
        await _inject_text(pool, client_id, str(content))


async def _handle_abort(
    websocket: WebSocket,
    client_id: str,
    session_id: str,
    payload: dict[str, Any],
) -> None:
    reason = payload.get("reason") or "client_abort"
    connection_manager.set_state(client_id, "aborted")
    # Local stop frame; full fan-out to preprocess/agent/speaker is later phase.
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
        await websocket.send_text(
            json.dumps(
                {
                    "type": "server",
                    "status": "accepted",
                    "message": "broadcast_speak deferred to phase-4 TTS pipeline",
                    "content": {"action": "broadcast_speak"},
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


async def _reload_via_admin(pool: GrpcClientPool, reason: str) -> bool:
    message_id = uuid.uuid4().hex

    def _call():
        stub = admin_pb2_grpc.ModelAdminServiceStub(pool.channel(MODEL_ADMIN_SERVICE))
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
