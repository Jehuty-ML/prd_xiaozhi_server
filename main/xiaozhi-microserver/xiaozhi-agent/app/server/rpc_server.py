from __future__ import annotations

from loguru import logger

from xiaozhi_common.config import BaseServerConfig
from xiaozhi_common.constants import AGENT_SERVICE, DEFAULT_PORTS, SPEAKER_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.grpc.server import start_grpc_server
from xiaozhi_common.nacos.client import create_nacos_client
from xiaozhi_common.nacos.registry import NacosRegistry
from xiaozhi_common.nacos.resolver import ServiceResolver
from xiaozhi import audio_pb2_grpc
from app.server.agent_service import AgentServicer


def serve(config: BaseServerConfig) -> None:
    ip = config.get_local_ip()
    port = config.resolve_grpc_port(DEFAULT_PORTS[AGENT_SERVICE])
    nacos = create_nacos_client(config)
    registry = NacosRegistry(config, nacos, ip, port)
    try:
        registry.register()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Initial Nacos registration failed: {exc}")
    registry.start_heartbeat()

    resolver = ServiceResolver(config, nacos)
    resolver.watch(SPEAKER_SERVICE)
    pool = GrpcClientPool(resolver)

    def register(server):  # noqa: ANN001
        audio_pb2_grpc.add_AgentServiceServicer_to_server(AgentServicer(pool), server)

    server = start_grpc_server(
        port=port, max_workers=config.rpc_max_connect, register_fn=register
    )
    logger.info(f"{config.server_name} ready grpc={port}")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down agent...")
    finally:
        registry.stop()
        resolver.stop()
        pool.close()
