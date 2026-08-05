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
    """延迟取 logger，避免 import 期循环依赖；已有事件循环时不走 setup_logging。"""
    try:
        from loguru import logger as _logger

        return _logger.bind(tag=TAG)
    except Exception:
        class _Null:
            def info(self, *a, **k):
                pass

            def warning(self, *a, **k):
                pass

            def error(self, *a, **k):
                pass

        return _Null()

_DEFAULT_PHRASES = {
    "asr": "不好意思，我没听清楚，请再说一遍。",
    "asr_empty": "不好意思，我没听清楚，请再说一遍。",
    "llm": "主人，小智现在有点忙，我们稍后再试吧。",
    "tts": "抱歉，我暂时说不出话，请稍后再试。",
    "tool": "哎呀，网络遇到点问题，请稍后再试下！",
    "manage_api": "服务暂时不可用，请稍后再试。",
    "overload": "现在有点忙不过来，请稍后再试一下。",
}


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


def as_upstream_error(
    stage: str,
    exc: BaseException,
    *,
    kind: Optional[UpstreamKind] = None,
    retryable: Optional[bool] = None,
    message: str = "",
) -> UpstreamError:
    """把任意异常收敛为 UpstreamError（已是则原样返回）。"""
    if isinstance(exc, UpstreamError):
        return exc
    resolved = kind or classify_exception(exc)
    can_retry = (
        retryable if retryable is not None else is_retryable(resolved, None)
    )
    return UpstreamError(
        stage,
        resolved,
        message or str(exc),
        retryable=can_retry,
        cause=exc,
    )


def raise_as_upstream(
    stage: str,
    exc: BaseException,
    *,
    kind: Optional[UpstreamKind] = None,
    retryable: Optional[bool] = None,
    message: str = "",
) -> None:
    """边界强制抛 UpstreamError。"""
    raise as_upstream_error(
        stage, exc, kind=kind, retryable=retryable, message=message
    ) from exc


def ensure_non_empty(
    stage: str,
    value: Any,
    *,
    message: str = "empty response",
) -> Any:
    """空结果视为 EMPTY（不经熔断包装层时由调用方决定是否计入失败）。"""
    if value is None:
        raise UpstreamError(stage, UpstreamKind.EMPTY, message, retryable=False)
    if isinstance(value, str) and not value.strip():
        raise UpstreamError(stage, UpstreamKind.EMPTY, message, retryable=False)
    return value


def classify_exception(exc: BaseException) -> UpstreamKind:
    """将各家异常粗分到统一 kind。"""
    if isinstance(exc, UpstreamError):
        return exc.kind
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return UpstreamKind.TIMEOUT
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg or "deadline" in msg:
        return UpstreamKind.TIMEOUT
    if "overload" in msg or "busy" in msg:
        return UpstreamKind.OVERLOAD
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
    llm_timeout_seconds: float = 90.0
    tts_max_retries: int = 3
    tts_retry_delay_seconds: float = 0.3
    max_retries: int = 2
    retry_delay_seconds: float = 0.5
    circuit_failure_threshold: int = 5
    circuit_open_seconds: float = 30.0
    # 高级：LLM 主备 / TTS 预置音
    llm_fallback: str = ""
    tts_fallback_audio: str = "config/assets/tts_fallback.wav"
    use_tts_fallback_on_degrade: bool = True
    # 预算：TTFB=首 token；round=整轮（含流式输出），不是「ASR+LLM+TTS≤8s」
    llm_ttfb_deadline_seconds: float = 25.0
    round_deadline_seconds: float = 120.0
    # 过载背压
    overload_enabled: bool = True
    overload_max_concurrent_chats: int = 80
    overload_max_concurrent_llm: int = 80
    overload_tts_text_queue_threshold: int = 80
    overload_tts_audio_queue_threshold: int = 120
    # 有界队列容量（满则丢最旧）；默认与阈值一致，避免阈值永远达不到
    overload_tts_text_queue_maxsize: int = 80
    overload_tts_audio_queue_maxsize: int = 120
    overload_asr_audio_queue_maxsize: int = 200
    overload_report_queue_usage_threshold: float = 0.9
    # 混沌注入（仅联调；production 应保持 enabled=false）
    chaos_enabled: bool = False
    chaos_asr_fail_rate: float = 0.0
    chaos_llm_fail_rate: float = 0.0
    chaos_tts_fail_rate: float = 0.0
    # 多实例共享熔断（Redis）；默认关，未配/连不上时回退本地
    circuit_redis_enabled: bool = False
    circuit_redis_url: str = ""
    circuit_redis_host: str = "127.0.0.1"
    circuit_redis_port: int = 6379
    circuit_redis_password: str = ""
    circuit_redis_db: int = 1
    circuit_redis_key_prefix: str = "xiaozhi:circuit:"
    circuit_redis_fallback_local: bool = True
    circuit_redis_socket_timeout: float = 1.0
    # 按 stage/provider 覆盖：{"llm": {"ChatGLMLLM": {"timeout_seconds": 90}}}
    providers: Dict[str, Dict[str, Dict[str, Any]]] = None
    phrases: Dict[str, str] = None

    def __post_init__(self):
        if self.phrases is None:
            self.phrases = dict(_DEFAULT_PHRASES)
        if self.providers is None:
            self.providers = {}


def get_resilience_settings(config: Optional[dict] = None) -> ResilienceSettings:
    config = config or {}
    raw = (config.get("server") or {}).get("resilience") or {}
    if not isinstance(raw, dict):
        raw = {}

    phrases = dict(_DEFAULT_PHRASES)
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
        val = raw.get(key)
        # 话术必须是标量；同名若是 dict（如误配）则跳过
        if val is not None and not isinstance(val, dict):
            canon = key.replace("_failed", "") if key.endswith("_failed") else key
            phrases[canon] = str(val)

    overload = raw.get("overload") if isinstance(raw.get("overload"), dict) else {}
    # 过载话术：overload.message（与 overload.* 配置同树，不冲突）
    # 兼容旧键 overload_message / 顶层标量 overload
    om = overload.get("message")
    if om is None:
        om = raw.get("overload_message")
    if om is not None and not isinstance(om, dict):
        phrases["overload"] = str(om)
    elif isinstance(raw.get("overload"), str) and raw.get("overload"):
        phrases["overload"] = str(raw["overload"])
    chaos = raw.get("chaos") if isinstance(raw.get("chaos"), dict) else {}
    redis_cfg = raw.get("redis") if isinstance(raw.get("redis"), dict) else {}
    providers = raw.get("providers") if isinstance(raw.get("providers"), dict) else {}

    settings = ResilienceSettings(
        enabled=bool(raw.get("enabled", True)),
        asr_timeout_seconds=float(raw.get("asr_timeout_seconds", 15)),
        llm_timeout_seconds=float(raw.get("llm_timeout_seconds", 90)),
        tts_max_retries=max(1, int(raw.get("tts_max_retries", 3))),
        tts_retry_delay_seconds=float(raw.get("tts_retry_delay_seconds", 0.3)),
        max_retries=max(0, int(raw.get("max_retries", 2))),
        retry_delay_seconds=float(raw.get("retry_delay_seconds", 0.5)),
        circuit_failure_threshold=max(1, int(raw.get("circuit_failure_threshold", 5))),
        circuit_open_seconds=float(raw.get("circuit_open_seconds", 30)),
        llm_fallback=str(raw.get("llm_fallback") or "").strip(),
        tts_fallback_audio=str(
            raw.get("tts_fallback_audio", "config/assets/tts_fallback.wav") or ""
        ).strip(),
        use_tts_fallback_on_degrade=bool(raw.get("use_tts_fallback_on_degrade", True)),
        llm_ttfb_deadline_seconds=float(raw.get("llm_ttfb_deadline_seconds", 25)),
        round_deadline_seconds=float(raw.get("round_deadline_seconds", 120)),
        overload_enabled=bool(overload.get("enabled", raw.get("overload_enabled", True))),
        overload_max_concurrent_chats=max(
            0,
            int(
                overload.get(
                    "max_concurrent_chats",
                    raw.get("overload_max_concurrent_chats", 80),
                )
            ),
        ),
        overload_max_concurrent_llm=max(
            0,
            int(
                overload.get(
                    "max_concurrent_llm",
                    raw.get("overload_max_concurrent_llm", 80),
                )
            ),
        ),
        overload_tts_text_queue_threshold=int(
            overload.get(
                "tts_text_queue_threshold",
                raw.get("overload_tts_text_queue_threshold", 80),
            )
        ),
        overload_tts_audio_queue_threshold=int(
            overload.get(
                "tts_audio_queue_threshold",
                raw.get("overload_tts_audio_queue_threshold", 120),
            )
        ),
        overload_tts_text_queue_maxsize=max(
            1,
            int(
                overload.get(
                    "tts_text_queue_maxsize",
                    raw.get(
                        "overload_tts_text_queue_maxsize",
                        overload.get(
                            "tts_text_queue_threshold",
                            raw.get("overload_tts_text_queue_threshold", 80),
                        ),
                    ),
                )
            ),
        ),
        overload_tts_audio_queue_maxsize=max(
            1,
            int(
                overload.get(
                    "tts_audio_queue_maxsize",
                    raw.get(
                        "overload_tts_audio_queue_maxsize",
                        overload.get(
                            "tts_audio_queue_threshold",
                            raw.get("overload_tts_audio_queue_threshold", 120),
                        ),
                    ),
                )
            ),
        ),
        overload_asr_audio_queue_maxsize=max(
            1,
            int(
                overload.get(
                    "asr_audio_queue_maxsize",
                    raw.get("overload_asr_audio_queue_maxsize", 200),
                )
            ),
        ),
        overload_report_queue_usage_threshold=float(
            overload.get(
                "report_queue_usage_threshold",
                raw.get("overload_report_queue_usage_threshold", 0.9),
            )
        ),
        chaos_enabled=bool(chaos.get("enabled", False)),
        chaos_asr_fail_rate=max(0.0, min(1.0, float(chaos.get("asr_fail_rate", 0)))),
        chaos_llm_fail_rate=max(0.0, min(1.0, float(chaos.get("llm_fail_rate", 0)))),
        chaos_tts_fail_rate=max(0.0, min(1.0, float(chaos.get("tts_fail_rate", 0)))),
        circuit_redis_enabled=bool(
            redis_cfg.get("enabled", raw.get("circuit_redis_enabled", False))
        ),
        circuit_redis_url=str(
            redis_cfg.get("url", raw.get("circuit_redis_url", "")) or ""
        ).strip(),
        circuit_redis_host=str(
            redis_cfg.get("host", raw.get("circuit_redis_host", "127.0.0.1"))
            or "127.0.0.1"
        ),
        circuit_redis_port=int(
            redis_cfg.get("port", raw.get("circuit_redis_port", 6379)) or 6379
        ),
        circuit_redis_password=str(
            redis_cfg.get("password", raw.get("circuit_redis_password", "")) or ""
        ),
        circuit_redis_db=int(redis_cfg.get("db", raw.get("circuit_redis_db", 1)) or 1),
        circuit_redis_key_prefix=str(
            redis_cfg.get(
                "key_prefix",
                raw.get("circuit_redis_key_prefix", "xiaozhi:circuit:"),
            )
            or "xiaozhi:circuit:"
        ),
        circuit_redis_fallback_local=bool(
            redis_cfg.get(
                "fallback_local",
                raw.get("circuit_redis_fallback_local", True),
            )
        ),
        circuit_redis_socket_timeout=float(
            redis_cfg.get(
                "socket_timeout",
                raw.get("circuit_redis_socket_timeout", 1.0),
            )
            or 1.0
        ),
        providers=providers,
        phrases=phrases,
    )
    # 容量至少能涨到过载阈值，否则水位检测永远达不到
    if (
        settings.overload_tts_text_queue_maxsize
        < settings.overload_tts_text_queue_threshold
    ):
        settings.overload_tts_text_queue_maxsize = (
            settings.overload_tts_text_queue_threshold
        )
    if (
        settings.overload_tts_audio_queue_maxsize
        < settings.overload_tts_audio_queue_threshold
    ):
        settings.overload_tts_audio_queue_maxsize = (
            settings.overload_tts_audio_queue_threshold
        )
    return settings


def get_provider_policy(
    config: Optional[dict],
    stage: str,
    provider: str,
) -> Dict[str, Any]:
    """合并全局 resilience 与 providers.<stage>.<name|default> 覆盖。"""
    settings = get_resilience_settings(config)
    base: Dict[str, Any] = {
        "timeout_seconds": {
            "asr": settings.asr_timeout_seconds,
            "llm": settings.llm_timeout_seconds,
            "tts": None,
        }.get(stage),
        "max_retries": settings.max_retries
        if stage != "tts"
        else settings.tts_max_retries,
        "retry_delay_seconds": settings.retry_delay_seconds
        if stage != "tts"
        else settings.tts_retry_delay_seconds,
        "circuit_failure_threshold": settings.circuit_failure_threshold,
        "circuit_open_seconds": settings.circuit_open_seconds,
    }
    stage_map = (settings.providers or {}).get(stage) or {}
    if not isinstance(stage_map, dict):
        return base
    for key in (provider, "default"):
        override = stage_map.get(key)
        if isinstance(override, dict):
            base.update({k: v for k, v in override.items() if v is not None})
            break
    return base


@dataclass
class RoundBudget:
    """单轮对话时间预算。

    - llm_ttfb：从 chat 开始到首个 LLM chunk（可感知响应）
    - round：整轮上限（含流式续写），超时则结束本轮并降级/收尾
    """

    started_at: float
    llm_ttfb_deadline_seconds: float
    round_deadline_seconds: float
    first_token_at: Optional[float] = None

    @classmethod
    def start(cls, settings: ResilienceSettings) -> "RoundBudget":
        return cls(
            started_at=time.monotonic(),
            llm_ttfb_deadline_seconds=float(settings.llm_ttfb_deadline_seconds or 0),
            round_deadline_seconds=float(settings.round_deadline_seconds or 0),
        )

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def mark_first_token(self) -> None:
        if self.first_token_at is None:
            self.first_token_at = time.monotonic()

    def ttfb_remaining(self) -> Optional[float]:
        if self.llm_ttfb_deadline_seconds <= 0 or self.first_token_at is not None:
            return None
        return self.llm_ttfb_deadline_seconds - self.elapsed()

    def round_remaining(self) -> Optional[float]:
        if self.round_deadline_seconds <= 0:
            return None
        return self.round_deadline_seconds - self.elapsed()

    def ensure_ttfb(self) -> None:
        rem = self.ttfb_remaining()
        if rem is not None and rem <= 0:
            raise UpstreamError(
                "llm",
                UpstreamKind.TIMEOUT,
                f"llm ttfb deadline exceeded ({self.llm_ttfb_deadline_seconds}s)",
                retryable=True,
            )

    def ensure_round(self) -> None:
        rem = self.round_remaining()
        if rem is not None and rem <= 0:
            raise UpstreamError(
                "llm",
                UpstreamKind.TIMEOUT,
                f"round deadline exceeded ({self.round_deadline_seconds}s)",
                retryable=False,
            )


def next_with_timeout(iterator, timeout_seconds: Optional[float]):
    """带超时的 next()；超时抛 TimeoutError（用于 LLM TTFB）。"""
    if timeout_seconds is None or timeout_seconds <= 0:
        return next(iterator)

    box: Dict[str, Any] = {}

    def _run():
        try:
            box["value"] = next(iterator)
        except Exception as e:
            box["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_seconds)
    if t.is_alive():
        raise TimeoutError(f"iterator next timeout after {timeout_seconds:.1f}s")
    if "error" in box:
        raise box["error"]
    if "value" not in box:
        raise StopIteration
    return box["value"]


class InFlightLimiter:
    """进程内全局在途计数（chat / llm）。limit<=0 表示不限制。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Dict[str, int] = {}

    def count(self, name: str) -> int:
        with self._lock:
            return int(self._counts.get(name, 0))

    def try_acquire(self, name: str, limit: int) -> bool:
        if limit <= 0:
            return True
        with self._lock:
            cur = int(self._counts.get(name, 0))
            if cur >= limit:
                return False
            self._counts[name] = cur + 1
            new_val = self._counts[name]
        self._sync_metric(name, new_val)
        return True

    def release(self, name: str) -> None:
        with self._lock:
            cur = int(self._counts.get(name, 0))
            if cur <= 0:
                self._counts[name] = 0
                new_val = 0
            else:
                new_val = cur - 1
                self._counts[name] = new_val
        self._sync_metric(name, new_val)

    def reset_for_tests(self) -> None:
        with self._lock:
            self._counts.clear()
        for name in ("chat", "llm"):
            self._sync_metric(name, 0)

    @staticmethod
    def _sync_metric(name: str, value: int) -> None:
        try:
            from core.utils import metrics as metrics_mod

            metrics_mod.set_inflight(name, value)
        except Exception:
            pass


_INFLIGHT = InFlightLimiter()


def get_inflight_count(name: str) -> int:
    return _INFLIGHT.count(name)


def try_acquire_inflight(name: str, limit: int) -> bool:
    return _INFLIGHT.try_acquire(name, limit)


def release_inflight(name: str) -> None:
    _INFLIGHT.release(name)


def reset_inflight_for_tests() -> None:
    _INFLIGHT.reset_for_tests()


def check_system_overload(conn: Any) -> Optional[str]:
    """过载检测：全局在途对话/LLM + 本连接 TTS/上报队列。返回原因或 None。"""
    settings = get_resilience_settings(getattr(conn, "config", None))
    if not settings.enabled or not settings.overload_enabled:
        return None

    chat_limit = settings.overload_max_concurrent_chats
    if chat_limit > 0:
        n = get_inflight_count("chat")
        if n >= chat_limit:
            return f"in_flight_chats={n}/{chat_limit}"

    llm_limit = settings.overload_max_concurrent_llm
    if llm_limit > 0:
        n = get_inflight_count("llm")
        if n >= llm_limit:
            return f"in_flight_llm={n}/{llm_limit}"

    tts = getattr(conn, "tts", None)
    if tts is not None:
        try:
            tq = tts.tts_text_queue.qsize()
            if tq >= settings.overload_tts_text_queue_threshold:
                return f"tts_text_queue={tq}"
            aq = tts.tts_audio_queue.qsize()
            if aq >= settings.overload_tts_audio_queue_threshold:
                return f"tts_audio_queue={aq}"
        except Exception:
            pass

    rq = getattr(conn, "report_queue", None)
    if rq is not None:
        try:
            maxsize = int(getattr(rq, "maxsize", 0) or 0)
            if maxsize > 0:
                usage = rq.qsize() / maxsize
                if usage >= settings.overload_report_queue_usage_threshold:
                    return f"report_queue={rq.qsize()}/{maxsize}"
        except Exception:
            pass
    return None


def maybe_chaos_fail(stage: str, config: Optional[dict] = None) -> None:
    """混沌注入：按概率抛 UpstreamError，便于联调降级路径。生产务必关闭。"""
    import random

    settings = get_resilience_settings(config)
    if not settings.enabled or not settings.chaos_enabled:
        return
    rate = {
        "asr": settings.chaos_asr_fail_rate,
        "llm": settings.chaos_llm_fail_rate,
        "tts": settings.chaos_tts_fail_rate,
    }.get(stage, 0.0)
    if rate <= 0:
        return
    if random.random() < rate:
        _log().warning(f"混沌注入失败 stage={stage} rate={rate}")
        raise UpstreamError(
            stage,
            UpstreamKind.UNAVAILABLE,
            f"chaos injected failure for {stage}",
            retryable=False,
        )


def resolve_tts_fallback_audio(config: Optional[dict] = None) -> Optional[str]:
    """解析预置降级音频绝对/相对路径，不存在则返回 None。"""
    import os

    settings = get_resilience_settings(config)
    path = settings.tts_fallback_audio
    if not path:
        return None
    if os.path.isfile(path):
        return path
    try:
        from config.config_loader import get_project_dir

        full = os.path.join(get_project_dir(), path)
        if os.path.isfile(full):
            return full
    except Exception:
        pass
    return None


def should_use_tts_fallback_audio(conn: Any = None, config: Optional[dict] = None) -> bool:
    """降级时是否优先走预置音（配置开启，或 TTS 熔断已开）。"""
    cfg = config or (getattr(conn, "config", None) if conn else None)
    settings = get_resilience_settings(cfg)
    if not settings.enabled or not settings.tts_fallback_audio:
        return False
    if settings.use_tts_fallback_on_degrade:
        return resolve_tts_fallback_audio(cfg) is not None
    # 仅在 TTS 熔断开路时启用
    tts = getattr(conn, "tts", None) if conn else None
    if tts is not None and hasattr(tts, "_tts_circuit"):
        try:
            breaker = tts._tts_circuit()
            if breaker and breaker.is_open:
                return resolve_tts_fallback_audio(cfg) is not None
        except Exception:
            pass
    return False


def get_fallback_text(
    config: Optional[dict],
    stage: str,
    kind: UpstreamKind = UpstreamKind.UNAVAILABLE,
) -> str:
    settings = get_resilience_settings(config)
    phrases = settings.phrases or _DEFAULT_PHRASES
    if kind == UpstreamKind.OVERLOAD:
        return phrases.get("overload") or phrases.get("llm") or _DEFAULT_PHRASES["overload"]
    if stage == "asr" and kind == UpstreamKind.EMPTY:
        return phrases.get("asr_empty") or phrases.get("asr") or _DEFAULT_PHRASES["asr"]
    return (
        phrases.get(stage)
        or phrases.get("llm")
        or _DEFAULT_PHRASES.get(stage)
        or _DEFAULT_PHRASES["llm"]
    )


class CircuitBreaker:
    """计数熔断：连续失败达阈值开路，冷却后半开；可选 Redis 跨实例共享。"""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
        *,
        store: Any = None,
    ):
        from core.utils.circuit_store import get_local_store

        self.name = name
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._store = store if store is not None else get_local_store()
        self._sync_metric()

    def state(self) -> str:
        """无副作用的状态：closed / open / half_open。"""
        return self._store.state(self.name, self.open_seconds)

    def allow(self) -> bool:
        allowed = self._store.allow(self.name, self.open_seconds)
        self._sync_metric()
        return allowed

    def record_success(self) -> None:
        self._store.record_success(self.name)
        self._sync_metric()

    def record_failure(self) -> None:
        just_opened, failures = self._store.record_failure(
            self.name, self.failure_threshold, self.open_seconds
        )
        if just_opened:
            _log().warning(
                f"熔断开路: {self.name} failures={failures} "
                f"cooldown={self.open_seconds}s store={type(self._store).__name__}"
            )
        self._sync_metric()

    @property
    def is_open(self) -> bool:
        return self.state() == "open"

    def _sync_metric(self) -> None:
        try:
            from core.utils import metrics as metrics_mod

            metrics_mod.set_circuit_state(self.name, self.state())
        except Exception:
            pass


_breakers: Dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()
_breaker_store_fp: Optional[str] = None


def _circuit_store_fingerprint(settings: ResilienceSettings) -> str:
    return (
        f"redis={int(settings.circuit_redis_enabled)}|"
        f"{settings.circuit_redis_url}|{settings.circuit_redis_host}|"
        f"{settings.circuit_redis_port}|{settings.circuit_redis_db}|"
        f"{settings.circuit_redis_key_prefix}"
    )


def get_circuit(name: str, settings: Optional[ResilienceSettings] = None) -> CircuitBreaker:
    from core.utils.circuit_store import get_circuit_store

    settings = settings or ResilienceSettings()
    parts = name.split(":", 1)
    if len(parts) == 2:
        stage_map = (settings.providers or {}).get(parts[0]) or {}
        if isinstance(stage_map, dict):
            override = stage_map.get(parts[1]) or stage_map.get("default") or {}
            if isinstance(override, dict):
                if override.get("circuit_failure_threshold") is not None:
                    settings.circuit_failure_threshold = int(
                        override["circuit_failure_threshold"]
                    )
                if override.get("circuit_open_seconds") is not None:
                    settings.circuit_open_seconds = float(
                        override["circuit_open_seconds"]
                    )

    store = get_circuit_store(
        redis_enabled=settings.circuit_redis_enabled,
        url=settings.circuit_redis_url,
        host=settings.circuit_redis_host,
        port=settings.circuit_redis_port,
        password=settings.circuit_redis_password,
        db=settings.circuit_redis_db,
        key_prefix=settings.circuit_redis_key_prefix,
        fallback_local=settings.circuit_redis_fallback_local,
        socket_timeout=settings.circuit_redis_socket_timeout,
    )
    fp = _circuit_store_fingerprint(settings)

    global _breaker_store_fp
    with _breakers_lock:
        if _breaker_store_fp is not None and _breaker_store_fp != fp:
            _breakers.clear()
        _breaker_store_fp = fp
        breaker = _breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name,
                failure_threshold=settings.circuit_failure_threshold,
                open_seconds=settings.circuit_open_seconds,
                store=store,
            )
            _breakers[name] = breaker
        else:
            breaker.failure_threshold = settings.circuit_failure_threshold
            breaker.open_seconds = settings.circuit_open_seconds
            breaker._store = store
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

    maybe_chaos_fail(stage, config)

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

    maybe_chaos_fail(stage, config)
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



def is_tts_queue_overload_reason(reason: Optional[str]) -> bool:
    """TTS 文本/音频队列过载：再往队列塞降级消息只会雪崩。"""
    if not reason:
        return False
    return reason.startswith("tts_text_queue") or reason.startswith("tts_audio_queue")


def enqueue_file_degradation(
    conn: Any,
    sentence_id: str,
    text: str,
    audio_path: str,
    *,
    ensure_first: bool = False,
    ensure_last: bool = True,
) -> None:
    """通过 ContentType.FILE 播放本地预置音，不调用 text_to_speak。"""
    from core.providers.tts.dto.dto import (
        ContentType,
        SentenceType,
        TTSMessageDTO,
    )
    from core.utils.dialogue import Message

    if ensure_first:
        conn.tts.tts_text_queue.put(
            TTSMessageDTO(
                sentence_id=sentence_id,
                sentence_type=SentenceType.FIRST,
                content_type=ContentType.ACTION,
            )
        )
    conn.tts.store_tts_text(sentence_id, text)
    conn.tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=sentence_id,
            sentence_type=SentenceType.MIDDLE,
            content_type=ContentType.FILE,
            content_detail=text,
            content_file=audio_path,
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


def speak_degradation(
    conn: Any,
    stage: str,
    kind: UpstreamKind = UpstreamKind.UNAVAILABLE,
    *,
    overload_reason: Optional[str] = None,
) -> None:
    """独立一轮对设备播报降级话术（ASR 失败等，尚未进入 chat）。

    TTS 队列已过载时只记指标、不入队，避免继续堆队列。
    in_flight 等过载优先走本地预置音（不调用 text_to_speak）。
    """
    settings = get_resilience_settings(getattr(conn, "config", None))
    if not settings.enabled:
        return
    if getattr(conn, "stop_event", None) and conn.stop_event.is_set():
        return
    if getattr(conn, "client_abort", False):
        return
    if not getattr(conn, "tts", None):
        return

    # TTS 文本/音频队列已过载时：故意不入队、不播降级话。
    # 原因：现有 FILE/TEXT 降级与预置音播放都仍走 tts_text_queue / tts_audio_queue，
    # 再 put 只会顶掉或拉长积压，加重雪崩；此时静默 shed + 指标是当前管线下更稳的选择。
    # 若要可感知降级，需另做「绕过 TTS 队列、直接 WS 下发短预置 opus」的短路径后再接这里。
    if is_tts_queue_overload_reason(overload_reason):
        try:
            from core.utils import metrics as metrics_mod

            metrics_mod.observe_degraded(stage, kind.value)
        except Exception:
            pass
        _log().warning(
            f"过载跳过降级播报（TTS 队列已满） reason={overload_reason}"
        )
        return

    text = get_fallback_text(getattr(conn, "config", None), stage, kind)
    try:
        if not getattr(conn, "sentence_id", None):
            conn.sentence_id = str(uuid.uuid4().hex)
        sentence_id = conn.sentence_id
        audio_path = resolve_tts_fallback_audio(getattr(conn, "config", None))
        # in_flight / 上报队列过载：强制预置音短路径（有文件时）
        prefer_file = bool(overload_reason) or should_use_tts_fallback_audio(conn)
        if prefer_file and audio_path:
            enqueue_file_degradation(
                conn,
                sentence_id,
                text,
                audio_path,
                ensure_first=True,
                ensure_last=True,
            )
            try:
                from core.utils import metrics as metrics_mod

                metrics_mod.observe_degraded(stage, kind.value)
            except Exception:
                pass
            _log().info(
                f"降级预置音 stage={stage} kind={kind.value} file={audio_path}"
            )
            return

        from core.handle.intentHandler import speak_txt

        speak_txt(conn, text)
        try:
            from core.utils import metrics as metrics_mod

            metrics_mod.observe_degraded(stage, kind.value)
        except Exception:
            pass
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
    """chat 已排队 FIRST 后的降级：补 MIDDLE + LAST，避免设备卡在 speaking。"""
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
        audio_path = resolve_tts_fallback_audio(getattr(conn, "config", None))
        if should_use_tts_fallback_audio(conn) and audio_path:
            enqueue_file_degradation(
                conn,
                sentence_id,
                text,
                audio_path,
                ensure_first=False,
                ensure_last=ensure_last,
            )
            try:
                from core.utils import metrics as metrics_mod

                metrics_mod.observe_degraded(stage, kind.value)
            except Exception:
                pass
            _log().info(
                f"会话降级预置音 stage={stage} kind={kind.value} "
                f"sentence={sentence_id} file={audio_path}"
            )
            return

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
        try:
            from core.utils import metrics as metrics_mod

            metrics_mod.observe_degraded(stage, kind.value)
        except Exception:
            pass
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
