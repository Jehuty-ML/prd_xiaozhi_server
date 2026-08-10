from __future__ import annotations

from typing import Any


class MemoryProviderBase:
    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    async def save_memory(self, msgs, session_id=None):  # noqa: ANN001
        return None

    async def query_memory(self, query: str) -> str:
        return ""


class NoMemProvider(MemoryProviderBase):
    async def query_memory(self, query: str) -> str:
        return ""


def create_memory(config: dict[str, Any], selected_name: str | None = None) -> MemoryProviderBase:
    selected = selected_name or (config.get("selected_module") or {}).get("Memory") or "nomem"
    block = (config.get("Memory") or {}).get(selected) or {"type": "nomem"}
    mem_type = str(block.get("type") or "nomem").lower()
    try:
        from xiaozhi_common.provider_support import raise_if_unsupported

        raise_if_unsupported("Memory", mem_type, selected)
    except ImportError:  # pragma: no cover
        pass
    if mem_type == "nomem":
        return NoMemProvider(block)
    return NoMemProvider(block)
