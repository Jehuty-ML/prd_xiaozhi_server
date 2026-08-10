from __future__ import annotations

from typing import Any

from loguru import logger

from app.providers.asr.base import ASRProviderBase
from app.providers.asr.stub import StubASR

try:
    from xiaozhi_common.provider_aliases import resolve_block
except ImportError:  # pragma: no cover

    def resolve_block(config, kind, selected_name=None):  # type: ignore
        selected = (
            selected_name
            or (config.get("selected_module") or {}).get(kind)
            or "StubASR"
        )
        block = (config.get(kind) or {}).get(selected) or {"type": "stub"}
        return selected, selected, block


def create_asr(
    config: dict[str, Any], selected_name: str | None = None
) -> ASRProviderBase:
    selected, _key, block = resolve_block(config, "ASR", selected_name)
    if not block:
        block = {"type": "stub"}
    asr_type = str(block.get("type") or "stub").lower()
    try:
        from xiaozhi_common.provider_support import raise_if_unsupported

        raise_if_unsupported("ASR", asr_type, selected)
    except ImportError:  # pragma: no cover
        pass

    if asr_type in ("openai", "openai_compat", "whisper"):
        try:
            from app.providers.asr.openai_compat import OpenAICompatASR

            logger.info(f"ASR provider OpenAICompatASR selected={selected}")
            return OpenAICompatASR(block)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"OpenAICompatASR init failed ({exc}) — StubASR")
            return StubASR(block)

    if asr_type in ("fun_local", "funasr"):
        try:
            from app.providers.asr.fun_local import FunASR

            logger.info(f"ASR provider FunASR selected={selected}")
            return FunASR(block)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"FunASR init failed ({exc}) — StubASR")
            return StubASR(block)

    if asr_type == "doubao":
        appid = str(block.get("appid") or "").strip()
        token = str(block.get("access_token") or "").strip()
        if not appid or not token or "你的" in appid or "你的" in token:
            logger.warning(
                f"ASR {selected} missing appid/access_token — StubASR"
            )
            return StubASR(block)
        try:
            from app.providers.asr.doubao import DoubaoASR

            logger.info(f"ASR provider DoubaoASR selected={selected}")
            return DoubaoASR(block)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"DoubaoASR init failed ({exc}) — StubASR")
            return StubASR(block)

    logger.info(f"ASR provider StubASR selected={selected}")
    return StubASR(block)


__all__ = ["ASRProviderBase", "StubASR", "create_asr"]
