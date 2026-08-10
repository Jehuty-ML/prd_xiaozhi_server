"""Microserver-supported provider interface types (config_json.type).

Unknown types must fail fast instead of silently falling back to Echo/Stub.
"""

from __future__ import annotations

from typing import FrozenSet

# Keys are model_type (upper); values are allowed config_json.type (lower).
SUPPORTED_TYPES: dict[str, FrozenSet[str]] = {
    "LLM": frozenset({"echo", "openai", "openai_compat", "openai-compat"}),
    "ASR": frozenset(
        {"stub", "openai", "openai_compat", "whisper", "fun_local", "funasr", "doubao"}
    ),
    "TTS": frozenset({"echo", "edge", "edgetts", "doubao"}),
    "VAD": frozenset({"stub", "silero", "silero_vad"}),
    "Memory": frozenset({"nomem"}),
    "Intent": frozenset({"function_call", "nointent"}),
    "VLLM": frozenset({"openai", "openai_compat", "openai-compat"}),
}


def is_type_supported(kind: str, provider_type: str | None) -> bool:
    allowed = SUPPORTED_TYPES.get((kind or "").strip())
    if allowed is None:
        return True
    return str(provider_type or "").strip().lower() in allowed


def raise_if_unsupported(kind: str, provider_type: str | None, selected: str = "") -> None:
    if is_type_supported(kind, provider_type):
        return
    t = str(provider_type or "").strip() or "(empty)"
    sel = f" selected={selected}" if selected else ""
    allowed = ", ".join(sorted(SUPPORTED_TYPES.get(kind, frozenset())))
    raise ValueError(
        f"当前还不支持 {kind} 接口类型 '{t}'{sel}；"
        f"xiaozhi-microserver 已支持: {allowed}"
    )
