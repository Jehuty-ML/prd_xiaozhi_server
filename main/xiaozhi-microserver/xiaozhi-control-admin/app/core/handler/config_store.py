from __future__ import annotations

import copy
import json
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from xiaozhi_common.constants import (
    ACCESS_SERVICE,
    AGENT_SERVICE,
    PREPROCESS_SERVICE,
    RECEIVER_SERVICE,
    SPEAKER_SERVICE,
)
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.config_files import (
    redact_secrets,
    snapshot_for_peer,
)
from xiaozhi import admin_pb2, admin_pb2_grpc
from app.core.handler.manage_api_client import ManageApiClient

try:
    from xiaozhi_common.provider_aliases import mirror_provider_aliases
except ImportError:  # pragma: no cover

    def mirror_provider_aliases(config: dict) -> dict:  # type: ignore
        return config


def deep_merge(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    for key, value in (overlay or {}).items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def merge_api_config(
    local_config: dict,
    api_config: dict,
    default_server: Optional[dict] = None,
) -> dict:
    """Merge manage-api payload with local bind/auth/connection rules."""
    config_data = copy.deepcopy(api_config or {})
    config_data["read_config_from_api"] = True

    local_api = local_config.get("manager-api") or local_config.get("manager_api") or {}
    config_data["manager-api"] = {
        "url": local_api.get("url", ""),
        "secret": local_api.get("secret", ""),
        "enabled": local_api.get("enabled", True),
    }
    config_data["manager_api"] = dict(config_data["manager-api"])

    api_server = config_data.get("server") or {}
    if not isinstance(api_server, dict):
        api_server = {}
    custom_server = local_config.get("server") or {}
    if not isinstance(custom_server, dict):
        custom_server = {}
    default_server = default_server or {}
    if not isinstance(default_server, dict):
        default_server = {}

    bind_server = {}
    bind_server.update(default_server)
    bind_server.update(custom_server)

    merged_auth = {}
    if isinstance(default_server.get("auth"), dict):
        merged_auth.update(default_server["auth"])
    if isinstance(api_server.get("auth"), dict):
        merged_auth.update(api_server["auth"])
    if isinstance(custom_server.get("auth"), dict):
        merged_auth.update(custom_server["auth"])

    merged_server = {
        "ip": bind_server.get("ip", "0.0.0.0"),
        "port": bind_server.get("port", 8000),
        "http_port": bind_server.get("http_port", 8003),
        "vision_explain": bind_server.get("vision_explain", ""),
        "auth_key": bind_server.get("auth_key", ""),
        "auth": merged_auth,
        "websocket": bind_server.get(
            "websocket",
            api_server.get("websocket", "ws://127.0.0.1:8000/xiaozhi/v1/"),
        ),
    }

    for key in (
        "environment",
        "ota",
        "mcp_endpoint",
        "mqtt_gateway",
        "mqtt_signature_key",
        "udp_gateway",
        "mqtt_manager_api",
        "timezone_offset",
        "connection",
        "metrics",
        "health",
        "name",
        "phase",
        "resilience",
        "admin",
    ):
        if api_server.get(key) is not None:
            merged_server[key] = api_server.get(key)
        if key in custom_server:
            merged_server[key] = custom_server.get(key)

    if not merged_server.get("environment"):
        merged_server["environment"] = (
            custom_server.get("environment")
            or default_server.get("environment")
            or "development"
        )

    for section in ("connection", "metrics", "health", "resilience", "admin"):
        merged_section: dict = {}
        if isinstance(default_server.get(section), dict):
            merged_section.update(default_server[section])
        if isinstance(api_server.get(section), dict):
            merged_section.update(api_server[section])
        if isinstance(custom_server.get(section), dict):
            merged_section.update(custom_server[section])
        if merged_section:
            merged_server[section] = merged_section

    # Prefer local websocket URL for microserver coexistence.
    if custom_server.get("websocket"):
        merged_server["websocket"] = custom_server["websocket"]

    config_data["server"] = merged_server

    # Keep local selected_module / xiaozhi welcome unless API provides them.
    if "selected_module" not in config_data and "selected_module" in local_config:
        config_data["selected_module"] = local_config["selected_module"]
    if "xiaozhi" not in config_data and "xiaozhi" in local_config:
        config_data["xiaozhi"] = local_config["xiaozhi"]
    if not config_data.get("prompt_template") and local_config.get("prompt_template"):
        config_data["prompt_template"] = local_config["prompt_template"]
    # Dual-name: Doubao ↔ DoubaoASR/TTS/LLM, ChatGLM ↔ ChatGLMLLM, Stub* → Echo*
    return mirror_provider_aliases(config_data)


class ConfigStore:
    """Template config.yaml + private data/.config.yaml + optional manager-api."""

    def __init__(
        self,
        config_path: Optional[Path] = None,
        pool: Optional[GrpcClientPool] = None,
    ) -> None:
        service_root = Path(__file__).resolve().parents[3]
        self.service_root = service_root
        self.config_path = config_path or (service_root / "config.yaml")
        self.private_path = service_root / "data" / ".config.yaml"
        self.pool = pool
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict[str, Any], str], None]] = []
        self._template: dict[str, Any] = {}
        self._private: dict[str, Any] = {}
        self._data: dict[str, Any] = {
            "server": {
                "name": "xiaozhi-microserver",
                "phase": 2,
                "environment": "development",
                "http_port": 8003,
                "port": 8000,
                "websocket": "ws://127.0.0.1:8000/xiaozhi/v1/",
                "auth_key": "",
                "auth": {"enabled": False},
                "connection": {
                    "max_connections": 200,
                    "max_connections_per_device": 2,
                },
                "timezone_offset": 8,
            },
            "selected_module": {
                "ASR": "StubASR",
                "TTS": "EchoTTS",
                "LLM": "EchoLLM",
            },
            "xiaozhi": {
                "type": "hello",
                "version": 1,
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 24000,
                    "channels": 1,
                    "frame_duration": 60,
                },
            },
            "manager_api": {
                "url": "",
                "secret": "",
                "enabled": False,
            },
            "manager-api": {
                "url": "",
                "secret": "",
                "enabled": False,
            },
            "read_config_from_api": False,
        }
        self._api_client: Optional[ManageApiClient] = None
        self.reload(reason="startup", broadcast=False)

    def set_pool(self, pool: GrpcClientPool) -> None:
        self.pool = pool

    def add_listener(self, fn: Callable[[dict[str, Any], str], None]) -> None:
        self._listeners.append(fn)

    def _notify_local(self, reason: str) -> None:
        for fn in list(self._listeners):
            try:
                fn(self.get(), reason)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Config listener failed: {exc}")

    def _load_local_pair(self) -> dict[str, Any]:
        """config.yaml < data/.config.yaml (monolith order)."""
        from xiaozhi_common.config_files import read_yaml_file

        # Allow tests to inject config_path; private stays beside that file's service root.
        if self.config_path != self.service_root / "config.yaml":
            template_path = self.config_path
            private_path = self.config_path.parent / "data" / ".config.yaml"
        else:
            template_path = self.service_root / "config.yaml"
            private_path = self.service_root / "data" / ".config.yaml"

        template = read_yaml_file(template_path)
        private = read_yaml_file(private_path)
        self.config_path = template_path
        self.private_path = private_path
        self._template = template
        self._private = private
        merged = deep_merge(template, private) if private else dict(template)
        if template_path.exists():
            logger.info(f"Config template loaded from {template_path}")
        else:
            logger.warning(f"Config template missing: {template_path}")
        if private and private_path.exists():
            logger.info(f"Config private overlay loaded from {private_path}")
        return merged

    def reload(
        self, reason: str = "startup", *, broadcast: bool = True, pull_api: bool = True
    ) -> dict[str, Any]:
        with self._lock:
            local = self._load_local_pair()
            if local:
                self._data = deep_merge(self._data, local)
                api = self._data.get("manager-api") or self._data.get("manager_api") or {}
                self._data["manager-api"] = dict(api)
                self._data["manager_api"] = dict(api)
                logger.info(
                    f"Config merged local template+private reason={reason} "
                    f"private={self.private_path.exists()}"
                )
            else:
                logger.info("Config files missing; using in-memory defaults")

            self._api_client = ManageApiClient(self._data)

            broadcast_result: dict[str, Any] = {}
            if pull_api and self._api_client.enabled:
                try:
                    api_cfg = self._api_client.get_server_config_sync()
                    if api_cfg:
                        # Bind ports: template < private (local wins over API).
                        default_server = deep_merge(
                            dict(self._template.get("server") or {}),
                            dict(self._private.get("server") or {}),
                        )
                        self._data = merge_api_config(
                            self._data, api_cfg, default_server=default_server
                        )
                        self._data["read_config_from_api"] = True
                        logger.info("Config merged from manager-api")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"manager-api pull failed: {exc}")
            else:
                self._data = mirror_provider_aliases(self._data)

            snapshot = copy.deepcopy(self._data)

        self._notify_local(reason)
        if broadcast:
            broadcast_result = self.broadcast(reason=reason)
        snapshot["_broadcast"] = broadcast_result
        return snapshot

    def get(self, key: str = "", *, redact: bool = False) -> Any:
        with self._lock:
            if not key:
                data = copy.deepcopy(self._data)
                return redact_secrets(data) if redact else data
            cur: Any = self._data
            for part in key.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return None
                cur = cur[part]
            return copy.deepcopy(cur)

    def as_json(self, key: str = "", *, for_service: str = "") -> str:
        if for_service:
            with self._lock:
                full = copy.deepcopy(self._data)
            peer = snapshot_for_peer(full, for_service, access_service=ACCESS_SERVICE)
            if key:
                cur: Any = peer
                for part in key.split("."):
                    if not isinstance(cur, dict) or part not in cur:
                        cur = {}
                        break
                    cur = cur[part]
                return json.dumps(cur if cur is not None else {}, ensure_ascii=False)
            return json.dumps(peer, ensure_ascii=False)
        value = self.get(key)
        return json.dumps(value if value is not None else {}, ensure_ascii=False)

    def broadcast(self, reason: str = "reload") -> dict[str, Any]:
        """Push peer-scoped config snapshots (secrets redacted except access)."""
        if not self.pool:
            logger.debug("No gRPC pool; skip config broadcast")
            return {"access": "skipped"}

        message_id = uuid.uuid4().hex
        results: dict[str, Any] = {}
        peers = (
            ("access", ACCESS_SERVICE),
            ("agent", AGENT_SERVICE),
            ("receiver", RECEIVER_SERVICE),
            ("speaker", SPEAKER_SERVICE),
            ("preprocess", PREPROCESS_SERVICE),
        )

        def _apply(service_name: str, label: str) -> str:
            config_json = self.as_json(for_service=service_name)
            stub = admin_pb2_grpc.ConfigApplyServiceStub(self.pool.channel(service_name))
            resp = stub.ApplyConfig(
                admin_pb2.ApplyConfigRequest(
                    message_id=message_id,
                    config_json=config_json,
                    reason=reason,
                ),
                metadata=self.pool.metadata(message_id=message_id),
                timeout=10,
            )
            if int(resp.code) != 0:
                raise RuntimeError(resp.msg or "apply failed")
            return resp.result or "ok"

        for label, service_name in peers:
            try:
                results[label] = _apply(service_name, label)
                logger.info(f"Broadcast config to {label} ok reason={reason}")
            except Exception as exc:  # noqa: BLE001
                results[label] = f"error:{exc}"
                logger.warning(f"Broadcast to {label} failed: {exc}")
        return results
