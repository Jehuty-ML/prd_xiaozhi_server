from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import FastAPI, Response, WebSocket, WebSocketDisconnect
from loguru import logger

from xiaozhi import audio_pb2, audio_pb2_grpc
from xiaozhi_common.auth import AuthenticationError
from xiaozhi_common.constants import PREPROCESS_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common import metrics as metrics_mod
from xiaozhi_common.runtime_env import (
    allow_query_authorization,
    is_device_permitted,
    should_bypass_token_for_device,
)
from app.ws.connection_registry import ConnectionRejected
from app.ws.gateway_runtime import gateway_runtime
from app.ws.manager import connection_manager
from app.ws.protocol import dispatch_text


def create_app(pool: GrpcClientPool) -> FastAPI:
    app = FastAPI(title="xiaozhi-access", version="0.6.0")

    def _sync_gauges() -> None:
        reg = gateway_runtime.registry
        metrics_mod.set_ws_gauges(reg.active_count, reg.limits.max_connections)

    @app.get("/health")
    async def health():
        reg = gateway_runtime.registry
        _sync_gauges()
        return {
            "status": "ok",
            "service": "xiaozhi-access",
            "phase": 6,
            "active_connections": reg.active_count,
            "max_connections": reg.limits.max_connections,
            "rejected_total": reg.rejected_total,
        }

    @app.get("/ready")
    async def ready():
        reg = gateway_runtime.registry
        _sync_gauges()
        at_capacity = reg.active_count >= reg.limits.max_connections
        payload = {
            "status": "not_ready" if at_capacity else "ready",
            "service": "xiaozhi-access",
            "active_connections": reg.active_count,
            "max_connections": reg.limits.max_connections,
        }
        if at_capacity:
            from fastapi.responses import JSONResponse

            return JSONResponse(payload, status_code=503)
        return payload

    @app.get("/metrics")
    async def metrics():
        _sync_gauges()
        return Response(
            content=metrics_mod.render_latest(),
            media_type=metrics_mod.CONTENT_TYPE_LATEST,
        )

    @app.websocket("/xiaozhi/v1/")
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await _handle_websocket(pool, websocket)

    return app


async def _handle_websocket(pool: GrpcClientPool, websocket: WebSocket) -> None:
    # Auth before accept when possible (Starlette requires accept for some rejects).
    # We accept first, then auth/limit, then close with reason — matches FastAPI constraints.
    await websocket.accept()

    device_id, client_id = _extract_ids(websocket)
    _maybe_inject_query_auth(websocket)

    try:
        _authenticate(websocket, device_id, client_id)
    except AuthenticationError as exc:
        logger.warning(f"WS auth failed device={device_id}: {exc}")
        try:
            await websocket.send_text("认证失败")
            await websocket.close(code=1008, reason=str(exc)[:120])
        except Exception:  # noqa: BLE001
            pass
        return

    session_id = str(uuid.uuid4())
    acquired = False
    try:
        await gateway_runtime.registry.try_acquire(session_id, device_id)
        acquired = True
    except ConnectionRejected as exc:
        logger.warning(f"WS rejected device={device_id}: {exc.reason}")
        metrics_mod.observe_ws_rejected()
        try:
            await websocket.close(code=exc.close_code, reason=exc.reason[:120])
        except Exception:  # noqa: BLE001
            pass
        return

    # Prefer device-id as routing key when present (matches OTA/device identity).
    bind_id = device_id or client_id
    previous_ws = connection_manager.bind(
        bind_id,
        websocket,
        session_id=session_id,
        device_id=device_id,
        alias_id=client_id if client_id and client_id != bind_id else "",
    )
    if previous_ws is not None:
        try:
            await previous_ws.close(code=1000, reason="replaced by new connection")
        except Exception:  # noqa: BLE001
            pass
    metrics_mod.set_ws_gauges(
        gateway_runtime.registry.active_count,
        gateway_runtime.registry.limits.max_connections,
    )

    # Push protocol hello so clients that do not send hello still get session_id.
    welcome = gateway_runtime.welcome_message(session_id)
    await websocket.send_text(json.dumps(welcome, ensure_ascii=False))

    # Monolith equivalent: get_private_config_from_api → agent LLM/prompt per device.
    try:
        from app.ws.device_bind import bind_device_on_connect

        await bind_device_on_connect(
            pool,
            device_id=device_id,
            client_id=client_id,
            bind_id=bind_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"device bind/agent-models skipped: {exc}")

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "bytes" in message and message["bytes"] is not None:
                await _handle_audio(pool, bind_id, message["bytes"])
            elif "text" in message and message["text"] is not None:
                await dispatch_text(
                    pool=pool,
                    websocket=websocket,
                    client_id=bind_id,
                    session_id=session_id,
                    text=message["text"],
                )
    except WebSocketDisconnect:
        logger.info(f"WS disconnect client_id={bind_id}")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"WS error client_id={bind_id}: {exc}")
    finally:
        # Stop peer TTS/LLM/VAD and drop in-memory sessions for this device.
        try:
            from app.ws.protocol import _abort_peers

            await _abort_peers(pool, bind_id, reason="disconnect")
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"disconnect peer abort skipped: {exc}")
        connection_manager.unbind(websocket)
        if acquired:
            await gateway_runtime.registry.release(session_id)
        metrics_mod.set_ws_gauges(
            gateway_runtime.registry.active_count,
            gateway_runtime.registry.limits.max_connections,
        )


def _extract_ids(websocket: WebSocket) -> tuple[str, str]:
    qp = websocket.query_params
    headers = websocket.headers
    device_id = (
        qp.get("device-id")
        or qp.get("device_id")
        or headers.get("device-id")
        or headers.get("Device-Id")
        or ""
    )
    client_id = (
        qp.get("client-id")
        or qp.get("client_id")
        or headers.get("client-id")
        or headers.get("Client-Id")
        or device_id
        or f"dev-{uuid.uuid4().hex[:8]}"
    )
    if not device_id:
        device_id = client_id
    return device_id, client_id


def _maybe_inject_query_auth(websocket: WebSocket) -> None:
    if not allow_query_authorization(gateway_runtime.config):
        if websocket.query_params.get("authorization"):
            logger.warning("生产环境禁止从 URL query 传递 authorization，请使用 Header")
        return
    token = websocket.query_params.get("authorization")
    if token and "authorization" not in {k.lower() for k in websocket.headers.keys()}:
        # Starlette headers are immutable; stash on scope for auth reader.
        websocket.scope.setdefault("xiaozhi_auth", token)
        logger.warning("开发环境：已从 URL query 注入 authorization（生产环境将拒绝）")


def _authenticate(websocket: WebSocket, device_id: str, client_id: str) -> None:
    if not gateway_runtime.auth_enable:
        return
    if not is_device_permitted(
        gateway_runtime.config, device_id, gateway_runtime.allowed_devices
    ):
        raise AuthenticationError("Device not in allowlist")
    if should_bypass_token_for_device(
        gateway_runtime.config, device_id, gateway_runtime.allowed_devices
    ):
        return
    token = _extract_bearer(websocket)
    if not token:
        raise AuthenticationError("Missing or invalid Authorization header")
    ok = gateway_runtime.auth.verify_token(
        token, client_id=client_id, username=device_id
    )
    if not ok:
        raise AuthenticationError("Invalid token")


def _extract_bearer(websocket: WebSocket) -> Optional[str]:
    auth = websocket.headers.get("authorization") or websocket.headers.get(
        "Authorization"
    )
    if not auth:
        auth = websocket.scope.get("xiaozhi_auth") or ""
    if isinstance(auth, str) and auth.startswith("Bearer "):
        return auth[7:].strip()
    if isinstance(auth, str) and auth.strip():
        # query may pass raw token
        return auth.strip()
    return None


async def _handle_audio(pool: GrpcClientPool, client_id: str, data: bytes) -> None:
    import asyncio

    message_id = uuid.uuid4().hex
    meta = connection_manager.get_meta(client_id)
    listen_mode = str(meta.get("listen_mode") or "auto")
    aec_enabled = bool(meta.get("aec_enabled"))
    # Device WS uplink is typically Opus; keep "opus" so preprocess can decode.
    # If payload already looks like PCM (even length, large frames), still ok —
    # preprocess OpusDecoder falls back to treating bytes as PCM on decode error.
    fmt = "opus"
    logger.debug(f"WS audio uplink client_id={client_id} bytes={len(data)}")
    stub = audio_pb2_grpc.AudioPreprocessServiceStub(pool.channel(PREPROCESS_SERVICE))
    timeout = pool.default_timeout("grpc")

    def _call():
        return stub.SendAudioChunk(
            audio_pb2.AudioChunkRequest(
                message_id=message_id,
                client_id=client_id,
                data=data,
                format=fmt,
                aec_enabled=aec_enabled,
                listen_mode=listen_mode,
            ),
            metadata=pool.metadata(client_id, message_id),
            timeout=timeout,
        )

    try:
        resp = await asyncio.to_thread(
            lambda: pool.call("preprocess", _call, provider="SendAudioChunk")
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Preprocess uplink failed client_id={client_id}: {exc}")
        return
    if resp.result:
        logger.info(f"Preprocess done client_id={client_id} result={resp.result!r}")
