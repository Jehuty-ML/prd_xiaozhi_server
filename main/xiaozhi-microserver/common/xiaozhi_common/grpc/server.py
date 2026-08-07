from __future__ import annotations

from typing import Any, Callable, Optional

import grpc
from loguru import logger


def start_grpc_server(
    *,
    port: int,
    max_workers: int,
    register_fn: Callable[[grpc.Server], None],
    interceptors: Optional[list] = None,
) -> grpc.Server:
    # Phase-1: skip custom interceptors (metadata still available via request fields)
    server = grpc.server(
        __import__("concurrent").futures.ThreadPoolExecutor(max_workers=max_workers),
        interceptors=interceptors or [],
    )
    register_fn(server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info(f"gRPC server listening on {port}")
    return server
