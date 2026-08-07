"""Receiver YAML config loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = SERVICE_ROOT / "config.yaml"


class ReceiverRuntimeConfig:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_CONFIG_PATH
        self.data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            logger.warning(f"Receiver config missing: {self.path}")
            self.data = {}
            return
        with self.path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            loaded = {}
        self.data = loaded
        logger.info(f"Receiver config loaded from {self.path}")

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def selected(self) -> dict[str, Any]:
        return self.data.get("selected_module") or {}


runtime_config = ReceiverRuntimeConfig()
