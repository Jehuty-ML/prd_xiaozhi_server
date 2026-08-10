from __future__ import annotations

from typing import Any

from loguru import logger

from app.providers.tts.base import TTSProviderBase
from app.providers.tts.echo import EchoTTS

try:
    from xiaozhi_common.provider_aliases import resolve_block
except ImportError:  # pragma: no cover

    def resolve_block(config, kind, selected_name=None):  # type: ignore
        selected = (
            selected_name
            or (config.get("selected_module") or {}).get(kind)
            or "EchoTTS"
        )
        block = (config.get(kind) or {}).get(selected) or {"type": "echo"}
        return selected, selected, block


def create_tts(config: dict[str, Any], selected_name: str | None = None) -> TTSProviderBase:
    selected, _key, block = resolve_block(config, "TTS", selected_name)
    if not block:
        block = {"type": "echo"}
    tts_type = str(block.get("type") or "echo").lower()

    if tts_type in ("edge", "edgetts"):
        try:
            from app.providers.tts.edge import EdgeTTS

            logger.info(f"TTS provider EdgeTTS selected={selected}")
            return EdgeTTS(block)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"EdgeTTS init failed ({exc}) — falling back to EchoTTS")
            return EchoTTS(block)

    if tts_type == "doubao":
        token = str(block.get("access_token") or "").strip()
        if not token or "你的" in token:
            logger.warning(f"TTS {selected} missing access_token — EchoTTS")
            return EchoTTS(block)
        try:
            from app.providers.tts.doubao import DoubaoTTS

            logger.info(f"TTS provider DoubaoTTS selected={selected}")
            return DoubaoTTS(block)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"DoubaoTTS init failed ({exc}) — EchoTTS")
            return EchoTTS(block)

    logger.info(f"TTS provider EchoTTS selected={selected}")
    return EchoTTS(block)


__all__ = ["TTSProviderBase", "EchoTTS", "create_tts"]
