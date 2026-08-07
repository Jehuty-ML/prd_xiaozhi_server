from __future__ import annotations

from typing import Any

from loguru import logger

from app.providers.asr.base import ASRProviderBase
from app.providers.asr.stub import StubASR


def create_asr(
    config: dict[str, Any], selected_name: str | None = None
) -> ASRProviderBase:
    selected = (
        selected_name
        or (config.get("selected_module") or {}).get("ASR")
        or "StubASR"
    )
    block = (config.get("ASR") or {}).get(selected) or {"type": "stub"}
    asr_type = str(block.get("type") or "stub").lower()
    if asr_type in ("openai", "openai_compat", "whisper"):
        try:
            from app.providers.asr.openai_compat import OpenAICompatASR

            logger.info(f"ASR provider OpenAICompatASR selected={selected}")
            return OpenAICompatASR(block)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"OpenAICompatASR init failed ({exc}) — StubASR")
            return StubASR(block)
    logger.info(f"ASR provider StubASR selected={selected}")
    return StubASR(block)


__all__ = ["ASRProviderBase", "StubASR", "create_asr"]
