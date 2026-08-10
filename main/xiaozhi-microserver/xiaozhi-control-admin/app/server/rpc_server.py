from __future__ import annotations

import asyncio
import threading

import uvicorn
from loguru import logger

from xiaozhi_common.config import BaseServerConfig
from xiaozhi_common.constants import (
    ACCESS_SERVICE,
    AGENT_SERVICE,
    DEFAULT_PORTS,
    CONTROL_ADMIN_HTTP_PORT,
    CONTROL_ADMIN_SERVICE,
    PREPROCESS_SERVICE,
    RECEIVER_SERVICE,
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
    grpc_port = config.resolve_grpc_port(DEFAULT_PORTS[CONTROL_ADMIN_SERVICE])
    http_port = config.http_port or CONTROL_ADMIN_HTTP_PORT

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
    resolver.watch(AGENT_SERVICE)
    resolver.watch(RECEIVER_SERVICE)
    resolver.watch(PREPROCESS_SERVICE)
    pool = GrpcClientPool(resolver)

    store = ConfigStore(pool=pool)

    def _startup_broadcast() -> None:
        # Peers may still be booting; retry a few times instead of one-shot warning.
        import time

        delays = (0.5, 2.0, 5.0)
        for attempt, delay in enumerate(delays, start=1):
            time.sleep(delay)
            results = store.broadcast(reason=f"startup_retry_{attempt}")
            failed = [
                name
                for name, value in (results or {}).items()
                if isinstance(value, str) and value.startswith("error:")
            ]
            if not failed:
                logger.info(f"Startup broadcast ok attempt={attempt}")
                return
            logger.warning(
                f"Startup broadcast attempt={attempt} still failing: {failed}"
            )
        logger.warning(
            "Startup broadcast incomplete; call POST /config/reload after peers are up"
        )

    threading.Thread(
        target=_startup_broadcast, daemon=True, name="admin-startup-broadcast"
    ).start()

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
        logger.info(f"xiaozhi-control-admin HTTP on {http_port}, gRPC on {grpc_port}")
        uv = uvicorn.Config(
            app, host="0.0.0.0", port=http_port, loop="asyncio", log_level="info"
        )
        server = uvicorn.Server(uv)
        loop.run_until_complete(server.serve())

    threading.Thread(target=run_http, daemon=True, name="admin-http").start()

    try:
        grpc_server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down control-admin...")
    finally:
        registry.stop()
        resolver.stop()
        pool.close()
