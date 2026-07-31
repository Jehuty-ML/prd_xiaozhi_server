"""上游依赖韧性：统一失败语义、有限重试、熔断、设备侧降级话术。"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, TypeVar

TAG = __name__

T = TypeVar("T")


def _log():
    """延迟取 logger，避免 import 期与 config_loader/manage_api 循环依赖。"""
    from config.logger import setup_logging

    return setup_logging().bind(tag=TAG)

_DEFAULT_PHRASES = {
    "asr": "不好意思，我没听清楚，请再说一遍。",
    "asr_empty": "不好意思，我没听清楚，请再说一遍。",
    "llm": "主人，小智现在有点忙，我们稍后再试吧。",
    "tts": "抱歉，我暂时说不出话，请稍后再试。",
    "tool": "哎呀，网络遇到点问题，请稍后再试下！",
    "manage_api": "服务暂时不可用，请稍后再试。",
}


class UpstreamKind(str, Enum):
    OK = "ok"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    BAD_RESPONSE = "bad_response"
    EMPTY = "empty"
    CIRCUIT_OPEN = "circuit_open"


class UpstreamError(Exception):
    """统一上游失败语义，供会话层做降级分支。"""

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


def classify_exception(exc: BaseException) -> UpstreamKind:
    """将各家异常粗分到统一 kind。"""
    if isinstance(exc, UpstreamError):
        return exc.kind
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return UpstreamKind.TIMEOUT
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return UpstreamKind.TIMEOUT
    if "429" in msg or "rate limit" in msg or "too many" in msg:
        return UpstreamKind.RATE_LIMITED
    if "503" in msg or "502" in msg or "unavailable" in msg or "connection" in msg:
        return UpstreamKind.UNAVAILABLE
    return UpstreamKind.UNAVAILABLE


def is_retryable(kind: UpstreamKind, exc: Optional[BaseException] = None) -> bool:
    if isinstance(exc, UpstreamError):
        return exc.retryable
    return kind in (
        UpstreamKind.TIMEOUT,
        UpstreamKind.UNAVAILABLE,
        UpstreamKind.RATE_LIMITED,
    )


@dataclass
class ResilienceSettings:
    enabled: bool = True
    asr_timeout_seconds: float = 15.0
    llm_timeout_seconds: float = 60.0
    tts_max_retries: int = 3
    tts_retry_delay_seconds: float = 0.3
    max_retries: int = 2
    retry_delay_seconds: float = 0.5
    circuit_failure_threshold: int = 5
    circuit_open_seconds: float = 30.0
    phrases: Dict[str, str] = None

    def __post_init__(self):
        if self.phrases is None:
            self.phrases = dict(_DEFAULT_PHRASES)


def get_resilience_settings(config: Optional[dict] = None) -> ResilienceSettings:
    config = config or {}
    raw = (config.get("server") or {}).get("resilience") or {}
    if not isinstance(raw, dict):
        raw = {}

    phrases = dict(_DEFAULT_PHRASES)
    # 兼容顶层 system_error_response
    system_err = config.get("system_error_response")
    if system_err:
        phrases["llm"] = system_err
    for key in (
        "asr",
        "asr_empty",
        "llm",
        "tts",
        "tool",
        "manage_api",
        "asr_failed",
        "llm_failed",
        "tts_failed",
        "tool_failed",
    ):
        if raw.get(key):
            # asr_failed → asr 等别名
            canon = key.replace("_failed", "") if key.endswith("_failed") else key
            phrases[canon] = str(raw[key])

    return ResilienceSettings(
        enabled=bool(raw.get("enabled", True)),
        asr_timeout_seconds=float(raw.get("asr_timeout_seconds", 15)),
        llm_timeout_seconds=float(raw.get("llm_timeout_seconds", 60)),
        tts_max_retries=max(1, int(raw.get("tts_max_retries", 3))),
        tts_retry_delay_seconds=float(raw.get("tts_retry_delay_seconds", 0.3)),
        max_retries=max(0, int(raw.get("max_retries", 2))),
        retry_delay_seconds=float(raw.get("retry_delay_seconds", 0.5)),
        circuit_failure_threshold=max(1, int(raw.get("circuit_failure_threshold", 5))),
        circuit_open_seconds=float(raw.get("circuit_open_seconds", 30)),
        phrases=phrases,
    )


def get_fallback_text(
    config: Optional[dict],
    stage: str,
    kind: UpstreamKind = UpstreamKind.UNAVAILABLE,
) -> str:
    settings = get_resilience_settings(config)
    phrases = settings.phrases or _DEFAULT_PHRASES
    if stage == "asr" and kind == UpstreamKind.EMPTY:
        return phrases.get("asr_empty") or phrases.get("asr") or _DEFAULT_PHRASES["asr"]
    return (
        phrases.get(stage)
        or phrases.get("llm")
        or _DEFAULT_PHRASES.get(stage)
        or _DEFAULT_PHRASES["llm"]
    )


class CircuitBreaker:
    """简单计数熔断：连续失败达阈值开路，冷却后半开放行一次。"""

    def __init__(self, name: str, failure_threshold: int = 5, open_seconds: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._failures = 0
        self._opened_at: Optional[float] = None
        self._half_open = False
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.open_seconds:
                self._half_open = True
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open = False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._half_open or self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()
                self._half_open = False
                _log().warning(
                    f"熔断开路: {self.name} failures={self._failures} "
                    f"cooldown={self.open_seconds}s"
                )

    @property
    def is_open(self) -> bool:
        return not self.allow()


_breakers: Dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_circuit(name: str, settings: Optional[ResilienceSettings] = None) -> CircuitBreaker:
    settings = settings or ResilienceSettings()
    with _breakers_lock:
        breaker = _breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name,
                failure_threshold=settings.circuit_failure_threshold,
                open_seconds=settings.circuit_open_seconds,
            )
            _breakers[name] = breaker
        else:
            breaker.failure_threshold = settings.circuit_failure_threshold
            breaker.open_seconds = settings.circuit_open_seconds
        return breaker


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
    """同步调用包装：熔断 → 有限重试 → UpstreamError。"""
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
            return result
        except Exception as e:
            last_exc = e
            kind = classify_exception(e)
            retryable = is_retryable(kind, e if isinstance(e, UpstreamError) else None)
            if attempt < retries and retryable:
                _log().warning(
                    f"{stage}/{provider} 失败将重试 "
                    f"({attempt + 1}/{retries}): {kind.value} {e}"
                )
                time.sleep(delay)
                continue
            if breaker:
                breaker.record_failure()
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


async def async_call_with_resilience(
    stage: str,
    func: Callable[[], Any],
    *,
    config: Optional[dict] = None,
    provider: str = "default",
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
    retry_delay: Optional[float] = None,
    use_circuit: bool = True,
) -> Any:
    """异步调用包装：熔断 → timeout → 有限重试 → UpstreamError。"""
    settings = get_resilience_settings(config)
    if not settings.enabled:
        result = func()
        if asyncio.iscoroutine(result):
            return await result
        return result

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
            if asyncio.iscoroutine(result):
                if timeout and timeout > 0:
                    result = await asyncio.wait_for(result, timeout=timeout)
                else:
                    result = await result
            if breaker:
                breaker.record_success()
            return result
        except Exception as e:
            last_exc = e
            kind = classify_exception(e)
            retryable = is_retryable(kind, e if isinstance(e, UpstreamError) else None)
            if attempt < retries and retryable:
                _log().warning(
                    f"{stage}/{provider} 异步失败将重试 "
                    f"({attempt + 1}/{retries}): {kind.value} {e}"
                )
                await asyncio.sleep(delay)
                continue
            if breaker:
                breaker.record_failure()
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


def speak_degradation(
    conn: Any,
    stage: str,
    kind: UpstreamKind = UpstreamKind.UNAVAILABLE,
) -> None:
    """独立一轮对设备播报降级话术（ASR 失败等，尚未进入 chat）。"""
    settings = get_resilience_settings(getattr(conn, "config", None))
    if not settings.enabled:
        return
    if getattr(conn, "stop_event", None) and conn.stop_event.is_set():
        return
    if getattr(conn, "client_abort", False):
        return
    if not getattr(conn, "tts", None):
        return

    text = get_fallback_text(getattr(conn, "config", None), stage, kind)
    try:
        from core.handle.intentHandler import speak_txt

        if not getattr(conn, "sentence_id", None):
            conn.sentence_id = str(uuid.uuid4().hex)
        speak_txt(conn, text)
        _log().info(f"降级播报 stage={stage} kind={kind.value}: {text}")
    except Exception as e:
        _log().error(f"降级播报失败 stage={stage}: {e}")


def enqueue_chat_degradation(
    conn: Any,
    sentence_id: str,
    stage: str = "llm",
    kind: UpstreamKind = UpstreamKind.UNAVAILABLE,
    *,
    ensure_last: bool = True,
) -> None:
    """chat 已排队 FIRST 后的降级：补 MIDDLE 话术 + LAST，避免设备卡在 speaking。"""
    settings = get_resilience_settings(getattr(conn, "config", None))
    if not settings.enabled or not getattr(conn, "tts", None):
        return

    from core.providers.tts.dto.dto import (
        ContentType,
        SentenceType,
        TTSMessageDTO,
    )
    from core.utils.dialogue import Message

    text = get_fallback_text(getattr(conn, "config", None), stage, kind)
    try:
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sentence_id,
                sentence_type=SentenceType.MIDDLE,
                content_type=ContentType.TEXT,
                content_detail=text,
            )
        )
        if ensure_last:
            conn.tts.tts_text_queue.put(
                TTSMessageDTO(
                    sentence_id=sentence_id,
                    sentence_type=SentenceType.LAST,
                    content_type=ContentType.ACTION,
                )
            )
        if getattr(conn, "dialogue", None):
            conn.dialogue.put(Message(role="assistant", content=text))
        _log().info(
            f"会话降级 stage={stage} kind={kind.value} sentence={sentence_id}"
        )
    except Exception as e:
        _log().error(f"会话降级入队失败: {e}")


def metrics_status_for_kind(kind: UpstreamKind) -> str:
    """映射到 Prometheus status 标签（observe_provider 白名单）。"""
    if kind == UpstreamKind.OK:
        return "ok"
    if kind == UpstreamKind.EMPTY:
        return "empty"
    if kind in (UpstreamKind.TIMEOUT, UpstreamKind.CIRCUIT_OPEN):
        return "error"
    return "error"
