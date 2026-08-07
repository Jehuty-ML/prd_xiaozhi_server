from __future__ import annotations

from typing import Any, Callable, Optional, TypeVar

import grpc
from loguru import logger

from xiaozhi_common.nacos.resolver import ServiceResolver
from xiaozhi_common.resilience import (
    UpstreamError,
    call_with_resilience,
    get_resilience_settings,
    timeout_for_stage,
)

T = TypeVar("T")


class GrpcClientPool:
    def __init__(
        self,
        resolver: ServiceResolver,
        *,
        config: Optional[dict] = None,
    ) -> None:
        self.resolver = resolver
        self.config = config or {}
        self._channels: dict[str, grpc.Channel] = {}

    def set_config(self, config: dict) -> None:
        self.config = config or {}

    def channel(self, service_name: str) -> grpc.Channel:
        host, port = self.resolver.pick(service_name)
        key = f"{host}:{port}"
        if key not in self._channels:
            logger.debug(f"Open channel {service_name} -> {key}")
            self._channels[key] = grpc.insecure_channel(key)
        return self._channels[key]

    def metadata(self, client_id: str = "", message_id: str = "") -> list[tuple[str, str]]:
        md = []
        if client_id:
            md.append(("client_id", client_id))
        if message_id:
            md.append(("message_id", message_id))
        return md

    def call(
        self,
        stage: str,
        func: Callable[[], T],
        *,
        provider: str = "default",
        use_circuit: bool = True,
        max_retries: Optional[int] = None,
    ) -> T:
        """Run a sync gRPC callable with timeout/retry/circuit (phase-6)."""
        return call_with_resilience(
            stage,
            func,
            config=self.config,
            provider=provider,
            max_retries=max_retries,
            use_circuit=use_circuit,
        )

    def default_timeout(self, stage: str = "grpc") -> float:
        return timeout_for_stage(stage, self.config)

    def close(self) -> None:
        for ch in self._channels.values():
            ch.close()
        self._channels.clear()


def map_grpc_error(exc: BaseException, stage: str = "grpc") -> UpstreamError:
    """Normalize grpc.RpcError into UpstreamError."""
    from xiaozhi_common.resilience import UpstreamKind, classify_exception, is_retryable

    if isinstance(exc, UpstreamError):
        return exc
    if isinstance(exc, grpc.RpcError):
        code = exc.code() if hasattr(exc, "code") else None
        details = exc.details() if hasattr(exc, "details") else str(exc)
        if code == grpc.StatusCode.DEADLINE_EXCEEDED:
            kind = UpstreamKind.TIMEOUT
        elif code in (
            grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.RESOURCE_EXHAUSTED,
        ):
            kind = UpstreamKind.UNAVAILABLE
        else:
            kind = classify_exception(exc)
        return UpstreamError(
            stage, kind, details or str(exc), retryable=is_retryable(kind), cause=exc
        )
    kind = classify_exception(exc)
    return UpstreamError(
        stage, kind, str(exc), retryable=is_retryable(kind), cause=exc
    )
