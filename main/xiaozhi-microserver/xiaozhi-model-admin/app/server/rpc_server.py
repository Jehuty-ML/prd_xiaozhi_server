from __future__ import annotations

import asyncio
import threading

import uvicorn
from loguru import logger

from xiaozhi_common.config import BaseServerConfig
from xiaozhi_common.constants import (
    ACCESS_SERVICE,
    DEFAULT_PORTS,
    MODEL_ADMIN_HTTP_PORT,
    MODEL_ADMIN_SERVICE,
    SPEAKER_SERVICE,
)
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.grpc.server import start_grpc_server
from xiaozhi_common.nacos.client import create_nacos_client
from xiaozhi_common.nacos.registry import NacosRegistry
from xiaozhi_common.nacos.resolver import ServiceResolver
from xiaozhi import admin_pb2_grpc
from app.api.http_app import create_http_app
from app.core.handler.config_store import ConfigStore
from app.server.admin_service import ModelAdminServicer


def serve(config: BaseServerConfig) -> None:
    ip = config.get_local_ip()
    grpc_port = config.resolve_grpc_port(DEFAULT_PORTS[MODEL_ADMIN_SERVICE])
    http_port = config.http_port or MODEL_ADMIN_HTTP_PORT

    nacos = create_nacos_client(config)
    registry = NacosRegistry(config, nacos, ip, grpc_port)
    try:
        registry.register()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Initial Nacos registration failed: {exc}")
    registry.start_heartbeat()

    resolver = ServiceResolver(config, nacos)
    resolver.watch(ACCESS_SERVICE)
    resolver.watch(SPEAKER_SERVICE)
    pool = GrpcClientPool(resolver)

    store = ConfigStore(pool=pool)
    # Initial broadcast after access may still be starting; best-effort.
    try:
        store.broadcast(reason="startup")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Startup broadcast skipped: {exc}")

    def register(server):  # noqa: ANN001
        admin_pb2_grpc.add_ModelAdminServiceServicer_to_server(
            ModelAdminServicer(store), server
        )

    grpc_server = start_grpc_server(
        port=grpc_port, max_workers=config.rpc_max_connect, register_fn=register
    )

    app = create_http_app(store, pool=pool)

    def run_http() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.info(f"xiaozhi-model-admin HTTP on {http_port}, gRPC on {grpc_port}")
        uv = uvicorn.Config(
            app, host="0.0.0.0", port=http_port, loop="asyncio", log_level="info"
        )
        server = uvicorn.Server(uv)
        loop.run_until_complete(server.serve())

    threading.Thread(target=run_http, daemon=True, name="admin-http").start()

    try:
        grpc_server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down model-admin...")
    finally:
        registry.stop()
        resolver.stop()
        pool.close()
