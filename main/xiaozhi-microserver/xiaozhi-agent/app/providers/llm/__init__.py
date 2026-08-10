from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generator, Iterable, Optional


class LLMProviderBase(ABC):
    @abstractmethod
    def response(self, session_id: str, dialogue: list) -> Generator[str, None, None]:
        raise NotImplementedError

    def response_with_functions(
        self, session_id: str, dialogue: list, functions: Optional[list] = None
    ) -> Generator[tuple[Any, Any], None, None]:
        for token in self.response(session_id, dialogue):
            yield token, None


class EchoLLM(LLMProviderBase):
    """Dev fallback — no API key required."""

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    def response(self, session_id: str, dialogue: list) -> Generator[str, None, None]:
        user = ""
        for msg in reversed(dialogue or []):
            if msg.get("role") == "user" and msg.get("content"):
                user = str(msg["content"]).strip()
                break
        reply = f"小智收到：{user or '你好'}"
        yield reply

    def response_with_functions(
        self, session_id: str, dialogue: list, functions: Optional[list] = None
    ) -> Generator[tuple[Any, Any], None, None]:
        # After tool results, synthesize a spoken reply (no second tool call).
        for msg in reversed(dialogue or []):
            if msg.get("role") == "tool" and msg.get("content"):
                yield str(msg["content"]), None
                return
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                continue
            break

        user = ""
        for msg in reversed(dialogue or []):
            if msg.get("role") == "user" and msg.get("content"):
                content = str(msg["content"]).strip()
                if content.startswith("[系统提示]"):
                    continue
                user = content
                break
        lowered = user.lower()
        tool_names = {
            (f.get("function") or {}).get("name")
            for f in (functions or [])
            if isinstance(f, dict)
        }
        if "几点" in user or "时间" in user or "what time" in lowered:
            if "get_time" in tool_names:
                yield None, [_FakeToolCall("get_time", "{}")]
                return
        if "天气" in user or "weather" in lowered:
            if "get_weather" in tool_names:
                yield None, [
                    _FakeToolCall(
                        "get_weather",
                        '{"location":"杭州","lang":"zh_CN"}',
                    )
                ]
                return
        if any(w in user for w in ("再见", "拜拜", "退下", "晚安")):
            if "handle_exit_intent" in tool_names:
                yield None, [
                    _FakeToolCall(
                        "handle_exit_intent",
                        '{"say_goodbye":"再见，祝你今天愉快！"}',
                    )
                ]
                return
        yield f"小智收到：{user or '你好'}", None


class _FakeToolCall:
    """Mimics OpenAI streaming tool_call delta shape for EchoLLM."""

    def __init__(self, name: str, arguments: str) -> None:
        self.id = f"echo_{name}"
        self.type = "function"
        self.index = 0
        self.function = type("Fn", (), {"name": name, "arguments": arguments})()


class OpenAICompatLLM(LLMProviderBase):
    def __init__(self, config: dict) -> None:
        import httpx
        import openai

        self.model_name = config.get("model_name")
        self.api_key = config.get("api_key") or ""
        self.base_url = config.get("base_url") or config.get("url")
        # 智控台空字段常是 ""，不能原样传给 OpenAI/豆包。
        self.max_tokens = _optional_number(config.get("max_tokens"), as_int=True)
        self.temperature = _optional_number(config.get("temperature"))
        self.top_p = _optional_number(config.get("top_p"))
        self.frequency_penalty = _optional_number(config.get("frequency_penalty"))
        # Prefer short connect timeout so bad endpoints fail fast → fallback speech.
        read_timeout = float(config.get("timeout") or config.get("read_timeout") or 60)
        connect_timeout = float(config.get("connect_timeout") or 10)
        self.client = openai.OpenAI(
            api_key=self.api_key or "EMPTY",
            base_url=self.base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=min(30.0, read_timeout),
                pool=connect_timeout,
            ),
        )

    @staticmethod
    def _normalize(dialogue: list) -> list:
        for msg in dialogue:
            if "role" in msg and "content" not in msg:
                msg["content"] = ""
        return dialogue

    def _sampling_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for key, value in {
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
        }.items():
            if value is not None:
                params[key] = value
        return params

    def response(self, session_id: str, dialogue: list) -> Generator[str, None, None]:
        dialogue = self._normalize(dialogue)
        params: dict[str, Any] = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
            **self._sampling_params(),
        }
        stream = self.client.chat.completions.create(**params)
        try:
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                content = getattr(delta, "content", None) if delta else None
                if content:
                    yield content
        finally:
            stream.close()

    def response_with_functions(
        self, session_id: str, dialogue: list, functions: Optional[list] = None
    ) -> Generator[tuple[Any, Any], None, None]:
        dialogue = self._normalize(dialogue)
        params: dict[str, Any] = {
            "model": self.model_name,
            "messages": dialogue,
            "stream": True,
            "tools": functions or [],
            **self._sampling_params(),
        }
        stream = self.client.chat.completions.create(**params)
        try:
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                yield getattr(delta, "content", None), getattr(delta, "tool_calls", None)
        finally:
            stream.close()


def _optional_number(value: Any, *, as_int: bool = False) -> Optional[float | int]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if as_int:
        return int(number)
    return number


def create_llm(config: dict[str, Any], selected_name: str | None = None) -> LLMProviderBase:
    try:
        from xiaozhi_common.provider_aliases import resolve_block

        selected, _key, block = resolve_block(config, "LLM", selected_name)
    except ImportError:
        selected = (
            selected_name
            or (config.get("selected_module") or {}).get("LLM")
            or "EchoLLM"
        )
        block = (config.get("LLM") or {}).get(selected) or {"type": "echo"}
    if not block:
        block = {"type": "echo"}
    llm_type = str(block.get("type") or "echo").lower()
    try:
        from xiaozhi_common.provider_support import raise_if_unsupported

        raise_if_unsupported("LLM", llm_type, selected)
    except ImportError:  # pragma: no cover
        pass
    if llm_type in ("openai", "openai_compat", "openai-compat"):
        api_key = (block.get("api_key") or "").strip()
        if not api_key or "你的" in api_key:
            from loguru import logger

            logger.warning(
                f"LLM {selected} missing api_key — falling back to EchoLLM"
            )
            return EchoLLM(block)
        return OpenAICompatLLM(block)
    return EchoLLM(block)
