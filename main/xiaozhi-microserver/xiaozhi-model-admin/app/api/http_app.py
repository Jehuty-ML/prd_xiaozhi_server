from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from loguru import logger

from xiaozhi import command_pb2, command_pb2_grpc
from xiaozhi_common.constants import ACCESS_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.runtime_env import resolve_environment
from app.api.metrics import observe_reload, render_latest, set_access_gauges, CONTENT_TYPE_LATEST
from app.api.ota import OtaService
from app.api.vision import VisionService
from app.core.handler.config_store import ConfigStore


def create_http_app(
    store: ConfigStore, pool: Optional[GrpcClientPool] = None
) -> FastAPI:
    app = FastAPI(title="xiaozhi-model-admin", version="0.2.0")
    ota = OtaService(store.get)
    vision = VisionService(store.get)

    def _on_config(cfg: dict, reason: str) -> None:
        ota.apply_config(cfg, log=True)
        vision.apply_config(cfg)
        logger.debug(f"HTTP handlers refreshed after config reason={reason}")

    store.add_listener(_on_config)
    _on_config(store.get(), "startup")

    app.include_router(ota.create_router())
    app.include_router(vision.create_router())

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "xiaozhi-model-admin",
            "phase": 2,
            "environment": resolve_environment(store.get()),
        }

    @app.get("/ready")
    async def ready():
        checks: dict[str, Any] = {"http": {"ok": True}}
        access_stats = await _fetch_access_stats(pool)
        if access_stats is None:
            checks["access"] = {"ok": True, "skipped": True, "reason": "unreachable"}
            ready_ok = True
        else:
            active = int(access_stats.get("active", 0))
            max_conn = int(access_stats.get("max_connections", 0) or 0)
            set_access_gauges(active, max_conn or None)
            at_capacity = max_conn > 0 and active >= max_conn
            checks["access"] = {
                "ok": not at_capacity,
                "active": active,
                "max_connections": max_conn,
            }
            ready_ok = not at_capacity
        payload = {
            "status": "ready" if ready_ok else "not_ready",
            "service": "xiaozhi-model-admin",
            "checks": checks,
        }
        if not ready_ok:
            return JSONResponse(payload, status_code=503)
        return payload

    @app.get("/metrics")
    async def metrics():
        access_stats = await _fetch_access_stats(pool)
        if access_stats:
            set_access_gauges(
                int(access_stats.get("active", 0)),
                int(access_stats.get("max_connections", 0) or 0),
            )
        return Response(content=render_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/config")
    async def get_config(key: str = ""):
        return store.get(key) if key else store.get()

    @app.post("/config/reload")
    async def reload_config():
        try:
            snapshot = store.reload(reason="http", broadcast=True)
            observe_reload(True)
            return {
                "status": "reloaded",
                "broadcast": snapshot.get("_broadcast") or {},
            }
        except Exception as exc:  # noqa: BLE001
            observe_reload(False)
            return JSONResponse(
                {"status": "error", "message": str(exc)}, status_code=500
            )

    return app


async def _fetch_access_stats(pool: Optional[GrpcClientPool]) -> Optional[dict]:
    if not pool:
        return None
    message_id = uuid.uuid4().hex

    def _call():
        stub = command_pb2_grpc.AccessCommandServiceStub(pool.channel(ACCESS_SERVICE))
        return stub.GetConnectionStats(
            command_pb2.ConnectionStatsRequest(message_id=message_id),
            metadata=pool.metadata(message_id=message_id),
            timeout=3,
        )

    try:
        resp = await asyncio.to_thread(_call)
        if int(resp.code) != 0:
            return None
        return {
            "active": resp.active,
            "max_connections": resp.max_connections,
            "max_connections_per_device": resp.max_connections_per_device,
            "rejected_total": resp.rejected_total,
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"access stats unavailable: {exc}")
        return None
