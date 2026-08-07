from __future__ import annotations

import json
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from loguru import logger

from xiaozhi import audio_pb2, audio_pb2_grpc
from xiaozhi_common.constants import PREPROCESS_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool
from app.ws.manager import connection_manager


def create_app(pool: GrpcClientPool) -> FastAPI:
    app = FastAPI(title="xiaozhi-access", version="0.1.0")

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "xiaozhi-access",
            "active_connections": connection_manager.active_count,
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        client_id = (
            websocket.query_params.get("device-id")
            or websocket.query_params.get("client_id")
            or websocket.headers.get("device-id")
            or f"dev-{uuid.uuid4().hex[:8]}"
        )
        connection_manager.bind(client_id, websocket)
        await websocket.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "client_id": client_id,
                    "msg": "xiaozhi-access phase1 ready",
                },
                ensure_ascii=False,
            )
        )
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break
                if "bytes" in message and message["bytes"] is not None:
                    await _handle_audio(pool, client_id, message["bytes"])
                elif "text" in message and message["text"] is not None:
                    await _handle_text(pool, websocket, client_id, message["text"])
        except WebSocketDisconnect:
            logger.info(f"WS disconnect client_id={client_id}")
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"WS error client_id={client_id}: {exc}")
        finally:
            connection_manager.unbind(websocket)

    return app


async def _handle_audio(pool: GrpcClientPool, client_id: str, data: bytes) -> None:
    message_id = uuid.uuid4().hex
    logger.info(f"WS audio uplink client_id={client_id} bytes={len(data)}")
    stub = audio_pb2_grpc.AudioPreprocessServiceStub(pool.channel(PREPROCESS_SERVICE))
    # Blocking gRPC in thread to avoid blocking event loop
    import asyncio

    def _call():
        return stub.SendAudioChunk(
            audio_pb2.AudioChunkRequest(
                message_id=message_id,
                client_id=client_id,
                data=data,
                format="raw",
            ),
            metadata=pool.metadata(client_id, message_id),
            timeout=30,
        )

    resp = await asyncio.to_thread(_call)
    logger.info(f"Preprocess done client_id={client_id} result={resp.result!r}")


async def _handle_text(
    pool: GrpcClientPool, websocket: WebSocket, client_id: str, text: str
) -> None:
    text = text.strip()
    if not text:
        return
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and payload.get("type") == "ping":
            await websocket.send_text(json.dumps({"type": "pong"}))
            return
        if isinstance(payload, dict) and payload.get("type") == "listen":
            # inject text path for easy smoke test
            content = payload.get("text") or payload.get("data") or ""
            if content:
                await _inject_text(pool, client_id, content)
            return
        if isinstance(payload, dict) and "text" in payload:
            await _inject_text(pool, client_id, str(payload["text"]))
            return
    except json.JSONDecodeError:
        pass
    # Plain text -> treat as ASR inject
    await _inject_text(pool, client_id, text)


async def _inject_text(pool: GrpcClientPool, client_id: str, content: str) -> None:
    import asyncio
    import uuid as _uuid

    message_id = _uuid.uuid4().hex
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
