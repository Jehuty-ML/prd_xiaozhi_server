"""Template config.yaml + private data/.config.yaml loading (monolith-compatible)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml
from loguru import logger

from xiaozhi_common.admin_config import deep_merge


def read_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to read config {path}: {exc}")
        return {}
    return loaded if isinstance(loaded, dict) else {}


def resolve_config_paths(
    service_root: Path,
    *,
    template_name: str = "config.yaml",
    private_name: str = ".config.yaml",
) -> tuple[Path, Path]:
    """Return (template_path, private_path) under service_root / data/."""
    template = service_root / template_name
    private = service_root / "data" / private_name
    return template, private


def load_service_config(
    service_root: Path,
    *,
    template_name: str = "config.yaml",
    private_name: str = ".config.yaml",
) -> tuple[dict[str, Any], Path, Path, bool]:
    """Load template then overlay private data/.config.yaml.

    Returns (merged, template_path, private_path, private_loaded).
    Merge order matches monolith: config.yaml < data/.config.yaml
    """
    template_path, private_path = resolve_config_paths(
        service_root, template_name=template_name, private_name=private_name
    )
    template = read_yaml_file(template_path)
    private = read_yaml_file(private_path)
    merged = deep_merge(template, private) if private else dict(template)
    if template_path.exists():
        logger.info(f"Config template loaded from {template_path}")
    else:
        logger.warning(f"Config template missing: {template_path}")
    private_loaded = bool(private) and private_path.exists()
    if private_loaded:
        logger.info(f"Config private overlay loaded from {private_path}")
    return merged, template_path, private_path, private_loaded


def redact_secrets(config: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy with secrets blanked for broadcast / HTTP."""
    data = deep_merge({}, config)
    for key in ("manager-api", "manager_api"):
        api = data.get(key)
        if isinstance(api, dict) and "secret" in api:
            api = dict(api)
            api["secret"] = ""
            data[key] = api
    server = data.get("server")
    if isinstance(server, dict):
        server = dict(server)
        if "auth_key" in server:
            server["auth_key"] = ""
        admin = server.get("admin")
        if isinstance(admin, dict) and "token" in admin:
            admin = dict(admin)
            admin["token"] = ""
            server["admin"] = admin
        data["server"] = server
    rabbit = data.get("rabbitmq")
    if isinstance(rabbit, dict) and "password" in rabbit:
        rabbit = dict(rabbit)
        rabbit["password"] = ""
        data["rabbitmq"] = rabbit
    return data


def snapshot_for_peer(
    config: dict[str, Any],
    service_name: str,
    *,
    access_service: str = "",
) -> dict[str, Any]:
    """Peer-facing config snapshot.

    Only access keeps manager-api.secret (needed for agent-models).
    Other peers get redacted secrets so a compromised peer does not leak
    control-plane credentials.
    """
    if access_service and service_name == access_service:
        return deep_merge({}, config)
    return redact_secrets(config)
