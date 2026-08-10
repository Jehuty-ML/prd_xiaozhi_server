"""Provider selected_module / config-block dual-name aliases.

Short names (Doubao, ChatGLM) are preferred; long monolith / manager-api
names (DoubaoASR, DoubaoLLM, DoubaoTTS, ChatGLMLLM) remain compatible.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional

# kind -> alias_name -> ordered lookup keys (preferred first)
MODULE_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "ASR": {
        "Doubao": ("Doubao", "DoubaoASR"),
        "DoubaoASR": ("Doubao", "DoubaoASR"),
        "FunASR": ("FunASR",),
        "StubASR": ("StubASR",),
        "OpenAICompatASR": ("OpenAICompatASR",),
    },
    "TTS": {
        "Doubao": ("Doubao", "DoubaoTTS"),
        "DoubaoTTS": ("Doubao", "DoubaoTTS"),
        "EdgeTTS": ("EdgeTTS",),
        "EchoTTS": ("EchoTTS",),
        "StubTTS": ("EchoTTS", "StubTTS"),
    },
    "LLM": {
        "Doubao": ("Doubao", "DoubaoLLM"),
        "DoubaoLLM": ("Doubao", "DoubaoLLM"),
        "ChatGLM": ("ChatGLM", "ChatGLMLLM"),
        "ChatGLMLLM": ("ChatGLM", "ChatGLMLLM"),
        "EchoLLM": ("EchoLLM",),
        "StubLLM": ("EchoLLM", "StubLLM"),
        "OpenAICompatLLM": ("OpenAICompatLLM",),
    },
}

# Legacy selected_module values → preferred runtime name
SELECTED_NORMALIZE: dict[str, dict[str, str]] = {
    "ASR": {"DoubaoASR": "Doubao"},
    "TTS": {"DoubaoTTS": "Doubao", "StubTTS": "EchoTTS"},
    "LLM": {
        "DoubaoLLM": "Doubao",
        "ChatGLMLLM": "ChatGLM",
        "StubLLM": "EchoLLM",
    },
}


def lookup_keys(kind: str, selected: str) -> tuple[str, ...]:
    aliases = MODULE_ALIASES.get(kind) or {}
    if selected in aliases:
        return aliases[selected]
    return (selected,)


def resolve_block(
    config: dict[str, Any],
    kind: str,
    selected_name: Optional[str] = None,
) -> tuple[str, str, dict[str, Any]]:
    """Return (selected_name, block_key, block_dict)."""
    selected = (
        selected_name
        or (config.get("selected_module") or {}).get(kind)
        or ""
    )
    section = config.get(kind) or {}
    if not isinstance(section, dict):
        section = {}
    for key in lookup_keys(kind, selected):
        block = section.get(key)
        if isinstance(block, dict) and block:
            return selected, key, dict(block)
    # Empty / missing: still return preferred key for defaults
    keys = lookup_keys(kind, selected)
    preferred = keys[0] if keys else selected
    return selected, preferred, dict(section.get(preferred) or {})


def mirror_provider_aliases(config: dict[str, Any]) -> dict[str, Any]:
    """Copy provider blocks so short and long names both resolve.

    Mutates and returns ``config``. Also normalizes selected_module aliases
    to preferred short names when present.
    """
    selected = config.get("selected_module")
    if isinstance(selected, dict):
        normalized = dict(selected)
        for kind, mapping in SELECTED_NORMALIZE.items():
            cur = normalized.get(kind)
            if cur in mapping:
                normalized[kind] = mapping[cur]
        config["selected_module"] = normalized

    for kind, alias_map in MODULE_ALIASES.items():
        section = config.get(kind)
        if not isinstance(section, dict):
            continue
        # For each preferred short name that has siblings, mirror content
        seen_groups: set[tuple[str, ...]] = set()
        for keys in alias_map.values():
            if keys in seen_groups or len(keys) < 2:
                continue
            seen_groups.add(keys)
            donor: Optional[dict] = None
            for k in keys:
                block = section.get(k)
                if isinstance(block, dict) and block:
                    donor = block
                    break
            if not donor:
                continue
            for k in keys:
                existing = section.get(k)
                if not isinstance(existing, dict) or not existing:
                    section[k] = deepcopy(donor)
        config[kind] = section
    return config
