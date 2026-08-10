"""Provider selected_module / config-block dual-name aliases.

Short names (Doubao, ChatGLM) are preferred; long monolith / manager-api
names (DoubaoASR, DoubaoLLM, DoubaoTTS, ChatGLMLLM, LLM_DoubaoLLM) remain
compatible. When both a local placeholder and an API-filled block exist,
resolve/mirror prefer the block with real credentials.
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
    "VAD": {
        "SileroVAD": ("SileroVAD",),
        "VAD_SileroVAD": ("SileroVAD", "VAD_SileroVAD"),
        "StubVAD": ("StubVAD",),
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
    "ASR": {
        "DoubaoASR": "Doubao",
        "ASR_DoubaoASR": "Doubao",
    },
    "VAD": {
        "VAD_SileroVAD": "SileroVAD",
    },
    "TTS": {
        "DoubaoTTS": "Doubao",
        "TTS_DoubaoTTS": "Doubao",
        "StubTTS": "EchoTTS",
    },
    "LLM": {
        "DoubaoLLM": "Doubao",
        "LLM_DoubaoLLM": "Doubao",
        "ChatGLMLLM": "ChatGLM",
        "LLM_ChatGLMLLM": "ChatGLM",
        "StubLLM": "EchoLLM",
    },
}

_SECRET_KEYS = (
    "api_key",
    "access_token",
    "appid",
    "secret_key",
    "password",
    "token",
)


def _is_placeholder(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return "你的" in text or "placeholder" in lowered or text in {"***", "xxxx", "XXXX"}


def is_placeholder_credential(value: Any) -> bool:
    """True for empty / template credentials that must not overwrite real secrets."""
    return _is_placeholder(value)


SECRET_CREDENTIAL_KEYS = _SECRET_KEYS


def credential_score(block: dict[str, Any] | None) -> int:
    """Higher = more usable credentials (prefer manager-api filled blocks)."""
    if not isinstance(block, dict) or not block:
        return -1
    score = 0
    if block.get("type"):
        score += 1
    for key in _SECRET_KEYS:
        if key not in block:
            continue
        val = block.get(key)
        if _is_placeholder(val):
            continue
        score += 10
    # Prefer blocks that also carry endpoint / model identity.
    for key in ("base_url", "url", "model_name", "host"):
        if str(block.get(key) or "").strip():
            score += 1
    return score


def lookup_keys(kind: str, selected: str) -> tuple[str, ...]:
    aliases = MODULE_ALIASES.get(kind) or {}
    names: list[str] = []
    # manager-api often uses ids like ASR_DoubaoASR / LLM_DoubaoLLM
    prefix = f"{kind}_"
    stripped = selected[len(prefix) :] if selected.startswith(prefix) else selected
    for name in (selected, stripped):
        if not name:
            continue
        if name in aliases:
            names.extend(aliases[name])
        else:
            names.append(name)
    # unique, preserve order; also add KIND_name variants for manager-api ids
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        for candidate in (n, f"{kind}_{n}"):
            if candidate and candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return tuple(out) if out else (selected,)


def resolve_block(
    config: dict[str, Any],
    kind: str,
    selected_name: Optional[str] = None,
) -> tuple[str, str, dict[str, Any]]:
    """Return (selected_name, block_key, block_dict).

    Among alias keys, prefer the block with real credentials so local yaml
    placeholders (empty api_key) do not win over manager-api LLM_DoubaoLLM.
    """
    selected = (
        selected_name
        or (config.get("selected_module") or {}).get(kind)
        or ""
    )
    section = config.get(kind) or {}
    if not isinstance(section, dict):
        section = {}

    best_key = ""
    best_block: dict[str, Any] = {}
    best_score = -1
    for key in lookup_keys(kind, selected):
        block = section.get(key)
        if not isinstance(block, dict) or not block:
            continue
        score = credential_score(block)
        if score > best_score:
            best_score = score
            best_key = key
            best_block = dict(block)

    if best_key:
        return selected, best_key, best_block

    keys = lookup_keys(kind, selected)
    preferred = keys[0] if keys else selected
    return selected, preferred, dict(section.get(preferred) or {})


def mirror_provider_aliases(config: dict[str, Any]) -> dict[str, Any]:
    """Copy provider blocks so short and long names both resolve.

    Mutates and returns ``config``. Also normalizes selected_module aliases
    to preferred short names when present. Incomplete local placeholders are
    overwritten by richer alias siblings (e.g. LLM_DoubaoLLM with api_key).
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
        seen_groups: set[tuple[str, ...]] = set()
        for keys in alias_map.values():
            if keys in seen_groups or len(keys) < 2:
                continue
            seen_groups.add(keys)
            candidates = list(keys) + [f"{kind}_{k}" for k in keys]
            donor: Optional[dict] = None
            donor_score = -1
            for k in candidates:
                block = section.get(k)
                if not isinstance(block, dict) or not block:
                    continue
                score = credential_score(block)
                if score > donor_score:
                    donor_score = score
                    donor = block
            if not donor:
                continue
            for k in candidates:
                existing = section.get(k)
                if not isinstance(existing, dict) or not existing:
                    section[k] = deepcopy(donor)
                elif credential_score(existing) < donor_score:
                    section[k] = deepcopy(donor)
        config[kind] = section
    return config
