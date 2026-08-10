from __future__ import annotations

from typing import Any

from loguru import logger

from app.providers.vad.base import VADProviderBase
from app.providers.vad.stub import StubVAD


def create_vad(
    config: dict[str, Any], selected_name: str | None = None
) -> VADProviderBase:
    selected = (
        selected_name
        or (config.get("selected_module") or {}).get("VAD")
        or "StubVAD"
    )
    block = (config.get("VAD") or {}).get(selected) or {"type": "stub"}
    vad_type = str(block.get("type") or "stub").lower()
    try:
        from xiaozhi_common.provider_support import raise_if_unsupported

        raise_if_unsupported("VAD", vad_type, selected)
    except ImportError:  # pragma: no cover
        pass
    if vad_type in ("silero", "silero_vad"):
        try:
            from app.providers.vad.silero import SileroVAD

            logger.info(f"VAD provider SileroVAD selected={selected}")
            return SileroVAD(block)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"SileroVAD init failed ({exc}) — falling back to StubVAD")
            return StubVAD(block)
    logger.info(f"VAD provider StubVAD selected={selected}")
    return StubVAD(block)


__all__ = ["VADProviderBase", "StubVAD", "create_vad"]
