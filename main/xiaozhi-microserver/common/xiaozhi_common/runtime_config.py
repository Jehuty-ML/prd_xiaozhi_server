"""Shared runtime config loader: config.yaml < data/.config.yaml < admin remote."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from loguru import logger

from xiaozhi_common.admin_config import deep_merge
from xiaozhi_common.config_files import load_service_config

try:
    from xiaozhi_common.provider_aliases import mirror_provider_aliases
except ImportError:  # pragma: no cover

    def mirror_provider_aliases(config: dict) -> dict:  # type: ignore
        return config


class ServiceRuntimeConfig:
    """Per-service template + private overlay, with optional admin ApplyConfig merge."""

    def __init__(
        self,
        service_root: Path,
        *,
        log_label: str = "Service",
        selected_kind: str = "",
    ) -> None:
        self.service_root = service_root
        self.log_label = log_label
        self.selected_kind = selected_kind
        self.path = service_root / "config.yaml"
        self.private_path = service_root / "data" / ".config.yaml"
        self.data: dict[str, Any] = {}
        self._local: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        merged, template_path, private_path, _ = load_service_config(self.service_root)
        self.path = template_path
        self.private_path = private_path
        self._local = merged
        self.data = mirror_provider_aliases(dict(merged))
        if not template_path.exists() and not private_path.exists():
            logger.warning(f"{self.log_label} config missing under {self.service_root}")

    def apply_remote(self, remote: dict[str, Any], *, reason: str = "") -> None:
        if not isinstance(remote, dict) or not remote:
            return
        # Drop empty manager-api secret overlays so redacted broadcasts don't wipe local.
        remote = dict(remote)
        for key in ("manager-api", "manager_api"):
            api = remote.get(key)
            if isinstance(api, dict) and not str(api.get("secret") or "").strip():
                api = dict(api)
                # Keep url/enabled if present; ignore blank secret from redacted peer push.
                api.pop("secret", None)
                remote[key] = api
        merged = deep_merge(dict(self._local or {}), remote)
        self.data = mirror_provider_aliases(merged)
        selected = ""
        if self.selected_kind:
            selected = (self.data.get("selected_module") or {}).get(self.selected_kind)
        logger.info(
            f"{self.log_label} config applied from admin reason={reason or '-'} "
            f"{self.selected_kind or 'ok'}={selected or '-'}"
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def selected(self) -> dict[str, Any]:
        return self.data.get("selected_module") or {}
