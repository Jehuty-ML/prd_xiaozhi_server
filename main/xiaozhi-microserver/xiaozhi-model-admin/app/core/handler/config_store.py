from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import yaml
from loguru import logger


class ConfigStore:
    """Local YAML config stub; manager-api client reserved for phase 2."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        # app/core/handler/config_store.py -> service root is parents[3]
        self.config_path = config_path or Path(__file__).resolve().parents[3] / "config.yaml"
        self._data: dict[str, Any] = {
            "server": {"name": "xiaozhi-microserver", "phase": 1},
            "selected_module": {
                "ASR": "StubASR",
                "TTS": "StubTTS",
                "LLM": "StubLLM",
            },
            "manager_api": {
                "url": "",
                "secret": "",
                "enabled": False,
                "note": "Wire manage_api_client here in phase 2",
            },
        }
        self.reload()

    def reload(self, reason: str = "startup") -> dict[str, Any]:
        if self.config_path.exists():
            try:
                loaded = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    self._data.update(loaded)
                logger.info(f"Config loaded from {self.config_path} reason={reason}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Config load failed: {exc}")
        else:
            logger.info(f"Config file missing ({self.config_path}); using defaults")
        return self._data

    def get(self, key: str = "") -> Any:
        if not key:
            return self._data
        cur: Any = self._data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    def as_json(self, key: str = "") -> str:
        value = self.get(key)
        return json.dumps(value if value is not None else {}, ensure_ascii=False)


class ManagerApiClientStub:
    """Placeholder for manager-api integration (phase 2)."""

    def __init__(self, base_url: str = "", secret: str = "") -> None:
        self.base_url = base_url
        self.secret = secret

    def fetch_server_config(self) -> dict[str, Any]:
        logger.debug("ManagerApiClientStub.fetch_server_config not implemented")
        return {}

    def fetch_agent_models(self, device_id: str) -> dict[str, Any]:
        logger.debug(
            f"ManagerApiClientStub.fetch_agent_models device_id={device_id} not implemented"
        )
        return {}
