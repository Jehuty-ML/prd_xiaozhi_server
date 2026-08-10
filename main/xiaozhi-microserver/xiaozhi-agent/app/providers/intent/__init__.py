"""Intent providers — phase-3: nointent + function_call (intent_llm deferred)."""

from __future__ import annotations

from typing import Any


def resolve_intent_type(config: dict[str, Any]) -> str:
    selected = (config.get("selected_module") or {}).get("Intent") or "function_call"
    block = (config.get("Intent") or {}).get(selected) or {}
    intent_type = str(block.get("type") or selected or "function_call")
    try:
        from xiaozhi_common.provider_support import raise_if_unsupported

        raise_if_unsupported("Intent", intent_type, selected)
    except ImportError:  # pragma: no cover
        pass
    return intent_type


def intent_functions(config: dict[str, Any]) -> list[str]:
    selected = (config.get("selected_module") or {}).get("Intent") or "function_call"
    block = (config.get("Intent") or {}).get(selected) or {}
    funcs = block.get("functions") or []
    if not isinstance(funcs, list):
        try:
            funcs = list(funcs)
        except TypeError:
            funcs = []
    # Always available system tools
    necessary = ["handle_exit_intent", "get_time"]
    return list(dict.fromkeys(necessary + [str(x) for x in funcs]))
