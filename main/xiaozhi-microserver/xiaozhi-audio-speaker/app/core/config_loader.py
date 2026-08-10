"""Speaker YAML config loader."""

from __future__ import annotations

from pathlib import Path

from xiaozhi_common.runtime_config import ServiceRuntimeConfig

SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = SERVICE_ROOT / "config.yaml"

runtime_config = ServiceRuntimeConfig(
    SERVICE_ROOT, log_label="Speaker", selected_kind="TTS"
)
