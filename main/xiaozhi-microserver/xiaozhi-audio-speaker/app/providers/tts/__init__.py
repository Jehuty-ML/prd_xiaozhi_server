from __future__ import annotations

from typing import Any

from loguru import logger

from app.providers.tts.base import TTSProviderBase
from app.providers.tts.echo import EchoTTS


def create_tts(config: dict[str, Any], selected_name: str | None = None) -> TTSProviderBase:
    selected = (
        selected_name
        or (config.get("selected_module") or {}).get("TTS")
        or "EchoTTS"
    )
    block = (config.get("TTS") or {}).get(selected) or {"type": "echo"}
    tts_type = str(block.get("type") or "echo").lower()
    if tts_type in ("edge", "edgetts"):
        try:
            from app.providers.tts.edge import EdgeTTS

            logger.info(f"TTS provider EdgeTTS selected={selected}")
            return EdgeTTS(block)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"EdgeTTS init failed ({exc}) — falling back to EchoTTS")
            return EchoTTS(block)
    logger.info(f"TTS provider EchoTTS selected={selected}")
    return EchoTTS(block)


__all__ = ["TTSProviderBase", "EchoTTS", "create_tts"]
