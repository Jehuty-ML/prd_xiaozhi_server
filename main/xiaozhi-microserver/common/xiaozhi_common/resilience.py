"""Upstream resilience: timeout / limited retry / circuit breaker (phase-6)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, TypeVar

from loguru import logger

T = TypeVar("T")


class UpstreamKind(str, Enum):
    OK = "ok"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    BAD_RESPONSE = "bad_response"
    EMPTY = "empty"
    CIRCUIT_OPEN = "circuit_open"
    OVERLOAD = "overload"


class UpstreamError(Exception):
    def __init__(
        self,
        stage: str,
        kind: UpstreamKind = UpstreamKind.UNAVAILABLE,
        message: str = "",
        *,
        retryable: bool = False,
        cause: Optional[BaseException] = None,
    ):
        self.stage = stage
        self.kind = kind if isinstance(kind, UpstreamKind) else UpstreamKind(str(kind))
        self.retryable = retryable
        self.cause = cause
        super().__init__(message or f"{stage}:{self.kind.value}")


@dataclass
class ResilienceSettings:
    enabled: bool = True
    grpc_timeout_seconds: float = 30.0
    asr_timeout_seconds: float = 15.0
    llm_timeout_seconds: float = 90.0
    tts_timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_delay_seconds: float = 0.5
    circuit_failure_threshold: int = 5
    circuit_open_seconds: float = 30.0
    phrases: Dict[str, str] = field(
        default_factory=lambda: {
            "asr": "不好意思，我没听清楚，请再说一遍。",
            "llm": "主人，小智现在有点忙，我们稍后再试吧。",
            "tts": "抱歉，我暂时说不出话，请稍后再试。",
            "grpc": "服务暂时不可用，请稍后再试。",
            "overload": "现在有点忙不过来，请稍后再试一下。",
        }
    )


def get_resilience_settings(config: Optional[dict] = None) -> ResilienceSettings:
    config = config or {}
    raw = (config.get("server") or {}).get("resilience") or {}
    if not isinstance(raw, dict):
        raw = {}
    phrases = dict(ResilienceSettings().phrases)
    for key, val in (raw.get("phrases") or {}).items():
        if val is not None and not isinstance(val, dict):
            phrases[str(key)] = str(val)
    for key in ("asr", "llm", "tts", "grpc", "overload"):
        val = raw.get(key)
        if val is not None and not isinstance(val, dict):
            phrases[key] = str(val)
    return ResilienceSettings(
        enabled=bool(raw.get("enabled", True)),
        grpc_timeout_seconds=float(raw.get("grpc_timeout_seconds", 30)),
        asr_timeout_seconds=float(raw.get("asr_timeout_seconds", 15)),
        llm_timeout_seconds=float(raw.get("llm_timeout_seconds", 90)),
        tts_timeout_seconds=float(raw.get("tts_timeout_seconds", 60)),
        max_retries=max(0, int(raw.get("max_retries", 2))),
        retry_delay_seconds=float(raw.get("retry_delay_seconds", 0.5)),
        circuit_failure_threshold=max(1, int(raw.get("circuit_failure_threshold", 5))),
        circuit_open_seconds=float(raw.get("circuit_open_seconds", 30)),
        phrases=phrases,
    )


def classify_exception(exc: BaseException) -> UpstreamKind:
    if isinstance(exc, UpstreamError):
        return exc.kind
    if isinstance(exc, (TimeoutError,)):
        return UpstreamKind.TIMEOUT
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg or "deadline" in msg:
        return UpstreamKind.TIMEOUT
    if "429" in msg or "rate limit" in msg:
        return UpstreamKind.RATE_LIMITED
    if "unavailable" in msg or "connection" in msg or "503" in msg or "502" in msg:
        return UpstreamKind.UNAVAILABLE
    return UpstreamKind.UNAVAILABLE


def is_retryable(kind: UpstreamKind) -> bool:
    return kind in (
        UpstreamKind.TIMEOUT,
        UpstreamKind.UNAVAILABLE,
        UpstreamKind.RATE_LIMITED,
    )


def get_fallback_text(
    config: Optional[dict], stage: str, kind: UpstreamKind = UpstreamKind.UNAVAILABLE
) -> str:
    settings = get_resilience_settings(config)
    phrases = settings.phrases or {}
    if kind == UpstreamKind.OVERLOAD:
        return phrases.get("overload") or phrases.get("llm") or "请稍后再试。"
    return phrases.get(stage) or phrases.get("grpc") or "服务暂时不可用，请稍后再试。"


class LocalCircuitStore:
    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def allow(self, name: str, open_seconds: float) -> bool:
        with self._lock:
            entry = self._data.get(name)
            if not entry or entry.get("opened_at") is None:
                return True
            return time.time() - float(entry["opened_at"]) >= open_seconds

    def record_success(self, name: str) -> None:
        with self._lock:
            self._data.pop(name, None)

    def record_failure(
        self, name: str, failure_threshold: int, open_seconds: float
    ) -> tuple[bool, int]:
        with self._lock:
            entry = self._data.setdefault(name, {"failures": 0, "opened_at": None})
            entry["failures"] = int(entry.get("failures") or 0) + 1
            opened_at = entry.get("opened_at")
            half_open = (
                opened_at is not None
                and time.time() - float(opened_at) >= open_seconds
            )
            just_opened = False
            if half_open or entry["failures"] >= failure_threshold:
                if opened_at is None or half_open:
                    just_opened = True
                entry["opened_at"] = time.time()
            return just_opened, int(entry["failures"])

    def state(self, name: str, open_seconds: float) -> str:
        with self._lock:
            entry = self._data.get(name)
            if not entry or entry.get("opened_at") is None:
                return "closed"
            if time.time() - float(entry["opened_at"]) >= open_seconds:
                return "half_open"
            return "open"

    def reset(self) -> None:
        with self._lock:
            self._data.clear()


_STORE = LocalCircuitStore()
_BREAKERS: Dict[str, "CircuitBreaker"] = {}
_BREAKERS_LOCK = threading.Lock()


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
        *,
        store: Optional[LocalCircuitStore] = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._store = store or _STORE

    def allow(self) -> bool:
        return self._store.allow(self.name, self.open_seconds)

    def record_success(self) -> None:
        self._store.record_success(self.name)
        try:
            from xiaozhi_common import metrics as metrics_mod

            metrics_mod.set_circuit_state(self.name, "closed")
        except Exception:  # noqa: BLE001
            pass

    def record_failure(self) -> None:
        just_opened, failures = self._store.record_failure(
            self.name, self.failure_threshold, self.open_seconds
        )
        if just_opened:
            logger.warning(
                f"circuit open: {self.name} failures={failures} "
                f"cooldown={self.open_seconds}s"
            )
        try:
            from xiaozhi_common import metrics as metrics_mod

            metrics_mod.set_circuit_state(self.name, self.state())
        except Exception:  # noqa: BLE001
            pass

    def state(self) -> str:
        return self._store.state(self.name, self.open_seconds)

    @property
    def is_open(self) -> bool:
        return self.state() == "open"


def get_circuit(
    name: str, settings: Optional[ResilienceSettings] = None
) -> CircuitBreaker:
    settings = settings or ResilienceSettings()
    with _BREAKERS_LOCK:
        breaker = _BREAKERS.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name,
                failure_threshold=settings.circuit_failure_threshold,
                open_seconds=settings.circuit_open_seconds,
            )
            _BREAKERS[name] = breaker
        else:
            breaker.failure_threshold = settings.circuit_failure_threshold
            breaker.open_seconds = settings.circuit_open_seconds
        return breaker


def reset_circuits_for_tests() -> None:
    with _BREAKERS_LOCK:
        _BREAKERS.clear()
    _STORE.reset()


def call_with_resilience(
    stage: str,
    func: Callable[[], T],
    *,
    config: Optional[dict] = None,
    provider: str = "default",
    max_retries: Optional[int] = None,
    retry_delay: Optional[float] = None,
    use_circuit: bool = True,
) -> T:
    """Sync wrapper: circuit → limited retry → UpstreamError."""
    settings = get_resilience_settings(config)
    if not settings.enabled:
        return func()

    circuit_name = f"{stage}:{provider}"
    breaker = get_circuit(circuit_name, settings) if use_circuit else None
    retries = settings.max_retries if max_retries is None else max_retries
    delay = settings.retry_delay_seconds if retry_delay is None else retry_delay

    if breaker and not breaker.allow():
        raise UpstreamError(
            stage,
            UpstreamKind.CIRCUIT_OPEN,
            f"{circuit_name} circuit open",
            retryable=False,
        )

    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        try:
            result = func()
            if breaker:
                breaker.record_success()
            try:
                from xiaozhi_common import metrics as metrics_mod

                metrics_mod.observe_upstream(stage, provider, "ok")
            except Exception:  # noqa: BLE001
                pass
            return result
        except Exception as e:  # noqa: BLE001
            last_exc = e
            kind = classify_exception(e)
            retryable = is_retryable(kind)
            if attempt < retries and retryable:
                logger.warning(
                    f"{stage}/{provider} retry {attempt + 1}/{retries}: {kind.value} {e}"
                )
                time.sleep(delay)
                continue
            if breaker:
                breaker.record_failure()
            try:
                from xiaozhi_common import metrics as metrics_mod

                metrics_mod.observe_upstream(stage, provider, kind.value)
            except Exception:  # noqa: BLE001
                pass
            if isinstance(e, UpstreamError):
                raise
            raise UpstreamError(
                stage, kind, str(e), retryable=retryable, cause=e
            ) from e

    if isinstance(last_exc, UpstreamError):
        raise last_exc
    raise UpstreamError(
        stage,
        classify_exception(last_exc) if last_exc else UpstreamKind.UNAVAILABLE,
        str(last_exc) if last_exc else "unknown",
        cause=last_exc,
    )


def timeout_for_stage(stage: str, config: Optional[dict] = None) -> float:
    settings = get_resilience_settings(config)
    return {
        "asr": settings.asr_timeout_seconds,
        "llm": settings.llm_timeout_seconds,
        "tts": settings.tts_timeout_seconds,
        "grpc": settings.grpc_timeout_seconds,
    }.get(stage, settings.grpc_timeout_seconds)
