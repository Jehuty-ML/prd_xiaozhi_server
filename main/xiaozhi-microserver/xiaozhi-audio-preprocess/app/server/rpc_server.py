from __future__ import annotations

from loguru import logger

from xiaozhi_common.config import BaseServerConfig
from xiaozhi_common.constants import (
    ACCESS_SERVICE,
    AGENT_SERVICE,
    DEFAULT_PORTS,
    PREPROCESS_SERVICE,
    RECEIVER_SERVICE,
    SPEAKER_SERVICE,
)
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.grpc.server import start_grpc_server
from xiaozhi_common.nacos.client import create_nacos_client
from xiaozhi_common.nacos.registry import NacosRegistry
from xiaozhi_common.nacos.resolver import ServiceResolver
from xiaozhi import audio_pb2_grpc

from app.core.config_loader import runtime_config
from app.core.listen_session import session_store
from app.providers.vad import create_vad
from app.server.preprocess_service import AudioPreprocessServicer


def serve(config: BaseServerConfig) -> None:
    runtime_config.reload()
    vad = create_vad(runtime_config.data)
    sample_rate = int(runtime_config.get("sample_rate") or 16000)
    min_asr = int(runtime_config.get("min_asr_pcm_bytes") or 19200)
    pre_roll = int(runtime_config.get("pre_roll_frames") or 10)

    ip = config.get_local_ip()
    port = config.resolve_grpc_port(DEFAULT_PORTS[PREPROCESS_SERVICE])
    nacos = create_nacos_client(config)
    registry = NacosRegistry(config, nacos, ip, port)
    try:
        registry.register()
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Initial Nacos registration failed: {exc}")
    registry.start_heartbeat()

    resolver = ServiceResolver(config, nacos)
    for name in (RECEIVER_SERVICE, AGENT_SERVICE, ACCESS_SERVICE, SPEAKER_SERVICE):
        resolver.watch(name)
    pool = GrpcClientPool(resolver, config=runtime_config.data)

    servicer = AudioPreprocessServicer(pool)
    session_store.configure(
        vad,
        sample_rate=sample_rate,
        min_asr_pcm_bytes=min_asr,
        pre_roll_frames=pre_roll,
        asr_fn=servicer._asr_recognize,
        forward_text_fn=servicer._forward_text,
        notify_fn=servicer._notify_listen,
        send_device_fn=servicer._send_to_device,
        abort_peers_fn=servicer._abort_peers,
    )

    def register(server):  # noqa: ANN001
        audio_pb2_grpc.add_AudioPreprocessServiceServicer_to_server(servicer, server)

    server = start_grpc_server(
        port=port, max_workers=config.rpc_max_connect, register_fn=register
    )
    logger.info(f"{config.server_name} ready grpc={port} (phase-6 VAD+resilience)")
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down preprocess...")
    finally:
        registry.stop()
        resolver.stop()
        pool.close()
