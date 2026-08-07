from __future__ import annotations

from loguru import logger

from xiaozhi_common.config import BaseServerConfig
from xiaozhi_common.constants import ACCESS_SERVICE, DEFAULT_PORTS, SPEAKER_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.grpc.server import start_grpc_server
from xiaozhi_common.nacos.client import create_nacos_client
from xiaozhi_common.nacos.registry import NacosRegistry
from xiaozhi_common.nacos.resolver import ServiceResolver
from xiaozhi import audio_pb2_grpc

from app.core.config_loader import runtime_config
from app.core.downlink import Downlink
from app.core.speak_session import session_store
from app.providers.tts import create_tts
from app.server.speaker_service import AudioSpeakerServicer


def serve(config: BaseServerConfig) -> None:
    runtime_config.reload()
    tts = create_tts(runtime_config.data)
    frame_ms = int(runtime_config.get("frame_duration_ms") or 60)

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
    pool = GrpcClientPool(resolver)
    session_store.configure(tts, Downlink(pool), frame_duration_ms=frame_ms)

    def register(server):  # noqa: ANN001
        audio_pb2_grpc.add_AudioSpeakerServiceServicer_to_server(
            AudioSpeakerServicer(pool), server
        )

    server = start_grpc_server(
        port=port, max_workers=config.rpc_max_connect, register_fn=register
    )
    logger.info(f"{config.server_name} ready grpc={port} (phase-4 TTS)")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down speaker...")
    finally:
        registry.stop()
        resolver.stop()
        pool.close()
