from __future__ import annotations

from loguru import logger

from xiaozhi_common.admin_config import (
    make_apply_config_servicer,
    pull_admin_config,
)
from xiaozhi_common.config import BaseServerConfig
from xiaozhi_common.constants import (
    ACCESS_SERVICE,
    CONTROL_ADMIN_SERVICE,
    DEFAULT_PORTS,
    PREPROCESS_SERVICE,
    SPEAKER_SERVICE,
)
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.grpc.server import start_grpc_server
from xiaozhi_common.nacos.client import create_nacos_client
from xiaozhi_common.nacos.registry import NacosRegistry
from xiaozhi_common.nacos.resolver import ServiceResolver
from xiaozhi import admin_pb2_grpc, audio_pb2_grpc

from app.core.aec_uplink import AecReferencePusher
from app.core.config_loader import runtime_config
from app.core.downlink import Downlink
from app.core.speak_session import session_store
from app.providers.tts import create_tts
from app.server.speaker_service import AudioSpeakerServicer


def serve(config: BaseServerConfig) -> None:
    runtime_config.reload()

    ip = config.get_local_ip()
    port = config.resolve_grpc_port(DEFAULT_PORTS[SPEAKER_SERVICE])
    nacos = create_nacos_client(config)
    registry = NacosRegistry(config, nacos, ip, port)
    try:
        registry.register()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Initial Nacos registration failed: {exc}")
    registry.start_heartbeat()

    resolver = ServiceResolver(config, nacos)
    resolver.watch(ACCESS_SERVICE)
    resolver.watch(PREPROCESS_SERVICE)
    resolver.watch(CONTROL_ADMIN_SERVICE)
    pool = GrpcClientPool(resolver)

    remote = pull_admin_config(pool, service_name=SPEAKER_SERVICE)
    if remote:
        runtime_config.apply_remote(remote, reason="startup_pull")

    def _reconfigure() -> None:
        tts = create_tts(runtime_config.data)
        frame_ms = int(runtime_config.get("frame_duration_ms") or 60)
        aec = AecReferencePusher(pool)
        push_aec = bool(runtime_config.get("push_aec_reference", True))
        session_store.configure(
            tts,
            Downlink(pool, aec_pusher=aec, push_aec=push_aec),
            frame_duration_ms=frame_ms,
        )

    _reconfigure()

    def _apply(cfg: dict, reason: str) -> None:
        runtime_config.apply_remote(cfg, reason=reason)
        _reconfigure()

    def register(server):  # noqa: ANN001
        audio_pb2_grpc.add_AudioSpeakerServiceServicer_to_server(
            AudioSpeakerServicer(pool), server
        )
        admin_pb2_grpc.add_ConfigApplyServiceServicer_to_server(
            make_apply_config_servicer(apply_fn=_apply), server
        )

    server = start_grpc_server(
        port=port, max_workers=config.rpc_max_connect, register_fn=register
    )
    logger.info(f"{config.server_name} ready grpc={port} (phase-4 TTS + AEC ref)")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down speaker...")
    finally:
        registry.stop()
        resolver.stop()
        pool.close()
