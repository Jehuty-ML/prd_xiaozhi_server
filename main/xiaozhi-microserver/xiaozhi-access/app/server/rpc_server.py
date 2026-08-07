from __future__ import annotations

import asyncio
import threading

import uvicorn
from loguru import logger

from xiaozhi_common.config import BaseServerConfig
from xiaozhi_common.constants import (
    ACCESS_HTTP_PORT,
    ACCESS_SERVICE,
    DEFAULT_PORTS,
    PREPROCESS_SERVICE,
)
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.grpc.server import start_grpc_server
from xiaozhi_common.nacos.client import create_nacos_client
from xiaozhi_common.nacos.registry import NacosRegistry
from xiaozhi_common.nacos.resolver import ServiceResolver
from xiaozhi import audio_pb2_grpc, command_pb2_grpc, session_pb2_grpc
from app.server.access_service import (
    AccessAudioServicer,
    AccessCommandServicer,
    AccessSessionServicer,
)
from app.ws.app_factory import create_app
from app.ws.manager import connection_manager


def serve(config: BaseServerConfig) -> None:
    ip = config.get_local_ip()
    grpc_port = config.resolve_grpc_port(DEFAULT_PORTS[ACCESS_SERVICE])
    http_port = config.http_port or ACCESS_HTTP_PORT

    nacos = create_nacos_client(config)
    registry = NacosRegistry(config, nacos, ip, grpc_port)
    try:
        registry.register()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Initial Nacos registration failed: {exc}")
    registry.start_heartbeat()

    resolver = ServiceResolver(config, nacos)
    resolver.watch(PREPROCESS_SERVICE)
    pool = GrpcClientPool(resolver)

    def register(server):  # noqa: ANN001
        audio_pb2_grpc.add_AccessAudioServiceServicer_to_server(
            AccessAudioServicer(), server
        )
        command_pb2_grpc.add_AccessCommandServiceServicer_to_server(
            AccessCommandServicer(), server
        )
        session_pb2_grpc.add_SessionServiceServicer_to_server(
            AccessSessionServicer(), server
        )

    grpc_server = start_grpc_server(
        port=grpc_port, max_workers=config.rpc_max_connect, register_fn=register
    )

    app = create_app(pool)

    def run_http() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        connection_manager.set_loop(loop)
        logger.info(f"xiaozhi-access HTTP/WS on {http_port}, gRPC on {grpc_port}")
        config_uv = uvicorn.Config(app, host="0.0.0.0", port=http_port, loop="asyncio", log_level="info")
        server = uvicorn.Server(config_uv)
        loop.run_until_complete(server.serve())

    http_thread = threading.Thread(target=run_http, daemon=True, name="access-http")
    http_thread.start()

    try:
        grpc_server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down access...")
    finally:
        registry.stop()
        resolver.stop()
        pool.close()
