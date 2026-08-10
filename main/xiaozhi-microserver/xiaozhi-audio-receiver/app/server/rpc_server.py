from __future__ import annotations

from loguru import logger

from xiaozhi_common.admin_config import (
    make_apply_config_servicer,
    pull_admin_config,
)
from xiaozhi_common.config import BaseServerConfig
from xiaozhi_common.constants import (
    CONTROL_ADMIN_SERVICE,
    DEFAULT_PORTS,
    RECEIVER_SERVICE,
)
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.grpc.server import start_grpc_server
from xiaozhi_common.nacos.client import create_nacos_client
from xiaozhi_common.nacos.registry import NacosRegistry
from xiaozhi_common.nacos.resolver import ServiceResolver
from xiaozhi import admin_pb2_grpc, audio_pb2_grpc

from app.core.config_loader import runtime_config
from app.providers.asr import create_asr
from app.server.audio_service import AudioReceiverServicer


def serve(config: BaseServerConfig) -> None:
    runtime_config.reload()

    ip = config.get_local_ip()
    port = config.resolve_grpc_port(DEFAULT_PORTS[RECEIVER_SERVICE])
    nacos = create_nacos_client(config)
    registry = NacosRegistry(config, nacos, ip, port)
    try:
        registry.register()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Initial Nacos registration failed: {exc}")
    registry.start_heartbeat()

    resolver = ServiceResolver(config, nacos)
    resolver.watch(CONTROL_ADMIN_SERVICE)
    pool = GrpcClientPool(resolver)

    remote = pull_admin_config(pool, service_name=RECEIVER_SERVICE)
    if remote:
        runtime_config.apply_remote(remote, reason="startup_pull")

    asr = create_asr(runtime_config.data)
    servicer = AudioReceiverServicer(asr)

    def _apply(cfg: dict, reason: str) -> None:
        runtime_config.apply_remote(cfg, reason=reason)
        servicer.asr = create_asr(runtime_config.data)

    def register(server):  # noqa: ANN001
        audio_pb2_grpc.add_AudioReceiverServiceServicer_to_server(servicer, server)
        admin_pb2_grpc.add_ConfigApplyServiceServicer_to_server(
            make_apply_config_servicer(apply_fn=_apply), server
        )

    server = start_grpc_server(
        port=port, max_workers=config.rpc_max_connect, register_fn=register
    )
    logger.info(f"{config.server_name} ready grpc={port} (phase-5 ASR)")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down receiver...")
    finally:
        registry.stop()
        resolver.stop()
        pool.close()
