"""OTA endpoints for control-admin (FastAPI port of xiaozhi-server OTAHandler)."""

from __future__ import annotations

import base64
import glob
import hashlib
import hmac
import json
import os
import re
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from loguru import logger

from xiaozhi_common.auth import AuthManager
from xiaozhi_common.runtime_env import (
    is_device_permitted,
    normalize_allowed_devices,
    resolve_auth_enabled,
    resolve_devices_allowlist_only,
    resolve_environment,
    resolve_whitelist_bypass_allowed,
    should_bypass_token_for_device,
)


def _safe_basename(filename: str) -> str:
    return os.path.basename(filename)


def _parse_version(ver: str) -> Tuple[int, ...]:
    parts = re.findall(r"\d+", ver)
    return tuple(int(p) for p in parts) if parts else (0,)


def _is_higher_version(a: str, b: str) -> bool:
    ta, tb = _parse_version(a), _parse_version(b)
    maxlen = max(len(ta), len(tb))
    for i in range(maxlen):
        ai = ta[i] if i < len(ta) else 0
        bi = tb[i] if i < len(tb) else 0
        if ai > bi:
            return True
        if ai < bi:
            return False
    return False


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


class OtaService:
    def __init__(self, get_config, bin_dir: Optional[Path] = None) -> None:
        self._get_config = get_config
        self.bin_dir = Path(bin_dir or Path.cwd() / "data" / "bin")
        self._bin_cache: Dict[str, Any] = {
            "updated_at": 0,
            "ttl": 30,
            "files_by_model": {},
        }
        self.apply_config(get_config(), log=True)

    def apply_config(self, config: dict, *, log: bool = False) -> None:
        self.config = config or {}
        server = self.config.get("server") or {}
        auth_config = server.get("auth") or {}
        self.auth_enable = resolve_auth_enabled(self.config)
        self.allowed_devices = normalize_allowed_devices(
            auth_config.get("allowed_devices")
        )
        self.whitelist_bypass = resolve_whitelist_bypass_allowed(self.config)
        self.devices_allowlist_only = resolve_devices_allowlist_only(self.config)
        secret_key = server.get("auth_key", "")
        expire_seconds = auth_config.get("expire_seconds")
        self.auth = AuthManager(secret_key=secret_key, expire_seconds=expire_seconds)
        self._bin_cache["ttl"] = int(self.config.get("firmware_cache_ttl", 30))
        if log:
            logger.info(
                f"OTA auth: {'enabled' if self.auth_enable else 'disabled'} "
                f"(env={resolve_environment(self.config)})"
            )

    def _cors(self, response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    def _websocket_url(self) -> str:
        server = self.config.get("server") or {}
        configured = str(server.get("websocket") or "")
        if configured and "你的" not in configured:
            return configured
        port = int(server.get("port", 8000))
        return f"ws://{_local_ip()}:{port}/xiaozhi/v1/"

    def _download_base(self) -> str:
        server = self.config.get("server") or {}
        vision = str(server.get("vision_explain") or "")
        if vision and "/mcp/vision/explain" in vision:
            return vision.replace("/mcp/vision/explain", "")
        http_port = int(server.get("http_port", 8003))
        return f"http://{_local_ip()}:{http_port}"

    def _refresh_bin_cache(self) -> None:
        now = int(time.time())
        ttl = int(self._bin_cache.get("ttl", 30))
        if now - int(self._bin_cache.get("updated_at", 0)) < ttl and self._bin_cache.get(
            "files_by_model"
        ):
            return
        files_by_model: Dict[str, List[Tuple[str, str]]] = {}
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        for path in glob.glob(str(self.bin_dir / "*.bin")):
            fname = os.path.basename(path)
            m = re.match(r"^(.+?)_([0-9][A-Za-z0-9\.\-_]*)\.bin$", fname)
            if not m:
                continue
            model, version = m.group(1), m.group(2)
            files_by_model.setdefault(model, []).append((version, fname))
        for items in files_by_model.values():
            items.sort(key=lambda it: _parse_version(it[0]), reverse=True)
        self._bin_cache["files_by_model"] = files_by_model
        self._bin_cache["updated_at"] = now

    def generate_password_signature(self, content: str, secret_key: str) -> str:
        try:
            digest = hmac.new(
                secret_key.encode("utf-8"), content.encode("utf-8"), hashlib.sha256
            ).digest()
            return base64.b64encode(digest).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"MQTT password signature failed: {exc}")
            return ""

    def create_router(self) -> APIRouter:
        router = APIRouter()

        @router.options("/xiaozhi/ota/")
        @router.options("/xiaozhi/ota/download/{filename}")
        async def ota_options():
            return self._cors(Response(status_code=204))

        @router.get("/xiaozhi/ota/")
        async def ota_get():
            msg = (
                "OTA接口运行正常，向设备发送的websocket地址是："
                f"{self._websocket_url()}"
            )
            return self._cors(PlainTextResponse(msg))

        @router.post("/xiaozhi/ota/")
        async def ota_post(request: Request):
            try:
                raw = await request.body()
                device_id = request.headers.get("device-id", "")
                client_id = request.headers.get("client-id", "")
                if not device_id or not client_id:
                    body = {"success": False, "message": "request error."}
                    return self._cors(JSONResponse(body))

                data_json = {}
                try:
                    data_json = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:  # noqa: BLE001
                    data_json = {}

                server = self.config.get("server") or {}
                device_model = ""
                for h in ("device-model", "device_model", "model"):
                    if h in request.headers:
                        device_model = request.headers.get(h, "").strip()
                        break
                if not device_model:
                    board = data_json.get("board") if isinstance(data_json, dict) else None
                    if isinstance(board, dict):
                        device_model = board.get("type", "")
                    elif isinstance(data_json, dict):
                        device_model = str(data_json.get("model") or "")
                if not device_model:
                    device_model = "default"

                device_version = ""
                for h in (
                    "device-version",
                    "device_version",
                    "firmware-version",
                    "app-version",
                    "application-version",
                ):
                    if h in request.headers:
                        device_version = request.headers.get(h, "").strip()
                        break
                if not device_version and isinstance(data_json, dict):
                    device_version = (
                        (data_json.get("application") or {}).get("version") or ""
                    )
                if not device_version:
                    device_version = "0.0.0"

                return_json: dict[str, Any] = {
                    "server_time": {
                        "timestamp": int(round(time.time() * 1000)),
                        "timezone_offset": int(server.get("timezone_offset", 8)) * 60,
                    },
                    "firmware": {"version": device_version, "url": ""},
                }

                mqtt_gateway = str(server.get("mqtt_gateway") or "").strip()
                # manager-api 偶发下发字面量 "null"；视为未配置，走 WebSocket 分支
                if mqtt_gateway.lower() in ("", "null", "none", "undefined"):
                    mqtt_gateway = ""
                if mqtt_gateway:
                    group_id = f"GID_{device_model}".replace(":", "_").replace(" ", "_")
                    mac_safe = device_id.replace(":", "_")
                    mqtt_client_id = f"{group_id}@@@{mac_safe}@@@{mac_safe}"
                    username = base64.b64encode(
                        json.dumps({"ip": "unknown"}).encode("utf-8")
                    ).decode("utf-8")
                    password = ""
                    signature_key = server.get("mqtt_signature_key", "")
                    if signature_key:
                        password = self.generate_password_signature(
                            mqtt_client_id + "|" + username, signature_key
                        )
                    return_json["mqtt"] = {
                        "endpoint": mqtt_gateway,
                        "client_id": mqtt_client_id,
                        "username": username,
                        "password": password,
                        "publish_topic": "device-server",
                        "subscribe_topic": f"devices/p2p/{mac_safe}",
                    }
                else:
                    token = ""
                    if self.auth_enable:
                        if not is_device_permitted(
                            self.config, device_id, self.allowed_devices
                        ):
                            body = {
                                "success": False,
                                "message": "device not in allowlist",
                            }
                            return self._cors(JSONResponse(body, status_code=403))
                        if should_bypass_token_for_device(
                            self.config, device_id, self.allowed_devices
                        ):
                            token = ""
                        else:
                            token = self.auth.generate_token(client_id, device_id)
                    return_json["websocket"] = {
                        "url": self._websocket_url(),
                        "token": token,
                    }

                try:
                    self._refresh_bin_cache()
                    candidates = self._bin_cache.get("files_by_model", {}).get(
                        device_model, []
                    )
                    for ver, fname in candidates:
                        if _is_higher_version(ver, device_version):
                            return_json["firmware"]["version"] = ver
                            return_json["firmware"]["url"] = (
                                f"{self._download_base()}/xiaozhi/ota/download/{fname}"
                            )
                            break
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"Firmware check failed: {exc}")

                return self._cors(JSONResponse(return_json))
            except Exception as exc:  # noqa: BLE001
                logger.error(f"OTA POST error: {exc}")
                return self._cors(
                    JSONResponse(
                        {"success": False, "message": "request error."},
                    )
                )

        @router.get("/xiaozhi/ota/download/{filename}")
        async def ota_download(filename: str):
            fname = _safe_basename(filename)
            if not re.match(r"^[A-Za-z0-9\.\-_]+\.bin$", fname):
                return self._cors(PlainTextResponse("invalid filename", status_code=400))
            file_path = (self.bin_dir / fname).resolve()
            bin_dir = self.bin_dir.resolve()
            if bin_dir not in file_path.parents and file_path != bin_dir:
                return self._cors(PlainTextResponse("forbidden", status_code=403))
            if not file_path.is_file():
                return self._cors(PlainTextResponse("file not found", status_code=404))
            resp = FileResponse(path=str(file_path), filename=fname)
            return self._cors(resp)

        return router
