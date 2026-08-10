from __future__ import annotations

from typing import Any

from loguru import logger

from app.providers.vad.base import VADProviderBase
from app.providers.vad.stub import StubVAD

try:
    from xiaozhi_common.provider_aliases import resolve_block
except ImportError:  # pragma: no cover

    def resolve_block(config, kind, selected_name=None):  # type: ignore
        selected = (
            selected_name
            or (config.get("selected_module") or {}).get(kind)
            or "StubVAD"
        )
        block = (config.get(kind) or {}).get(selected) or {"type": "stub"}
        return selected, selected, block


def create_vad(
    config: dict[str, Any], selected_name: str | None = None
) -> VADProviderBase:
    # Prefer alias-aware resolve (VAD_SileroVAD / SileroVAD from manager-api).
    selected, _key, block = resolve_block(config, "VAD", selected_name)
    if not block:
        # Fall back to local short-name blocks when only selected_module was pushed
        selected = (
            selected_name
            or (config.get("selected_module") or {}).get("VAD")
            or "StubVAD"
        )
        vad_map = config.get("VAD") or {}
        block = (
            vad_map.get(selected)
            or vad_map.get("SileroVAD")
            or vad_map.get("StubVAD")
            or {"type": "stub"}
        )
    vad_type = str(block.get("type") or "stub").lower()
    # manager-api id without type → silero when name contains silero
    if vad_type in ("", "stub") and "silero" in str(selected).lower():
        vad_type = "silero"
        if "model_dir" not in block:
            block = {
                **block,
                "type": "silero",
                "model_dir": block.get("model_dir")
                or "../models/snakers4_silero-vad",
            }
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
