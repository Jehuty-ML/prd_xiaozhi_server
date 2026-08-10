"""manager-api HTTP client (simplified port from xiaozhi-server)."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

import httpx
from loguru import logger


class DeviceNotFoundException(Exception):
    pass


class DeviceBindException(Exception):
    def __init__(self, bind_code: str):
        self.bind_code = bind_code
        super().__init__(f"设备绑定异常，绑定码: {bind_code}")


class ManageApiClient:
    def __init__(self, config: dict[str, Any]):
        # Accept both manager-api and manager_api keys.
        api = config.get("manager-api") or config.get("manager_api") or {}
        if not isinstance(api, dict):
            api = {}
        self.config = api
        self._secret = str(api.get("secret") or "")
        self.max_retries = int(api.get("max_retries", 3))
        self.retry_delay = float(api.get("retry_delay", 2))
        self._clients: dict[int, httpx.AsyncClient] = {}

    @property
    def enabled(self) -> bool:
        url = str(self.config.get("url") or "").strip()
        secret = self._secret.strip()
        if not url or not secret:
            return False
        if "你" in secret or "placeholder" in secret.lower():
            return False
        enabled = self.config.get("enabled")
        if enabled is False:
            return False
        return True

    async def _ensure_client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        if loop_id not in self._clients:
            limits = httpx.Limits(max_keepalive_connections=0)
            self._clients[loop_id] = httpx.AsyncClient(
                base_url=self.config.get("url"),
                headers={
                    "User-Agent": f"XiaozhiMicroAdmin/2.0 (PID:{os.getpid()})",
                    "Accept": "application/json",
                    "Authorization": "Bearer " + self._secret,
                },
                timeout=self.config.get("timeout", 30),
                limits=limits,
                trust_env=False,
            )
        return self._clients[loop_id]

    async def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        client = await self._ensure_client()
        endpoint = endpoint.lstrip("/")
        response = await client.request(method, endpoint, **kwargs)
        response.raise_for_status()
        result = response.json()
        code = result.get("code")
        if code == 10041:
            raise DeviceNotFoundException(result.get("msg"))
        if code == 10042:
            raise DeviceBindException(result.get("msg"))
        if code != 0:
            raise Exception(f"API返回错误: {result.get('msg', '未知错误')}")
        return result.get("data")

    def _should_retry(self, exception: Exception) -> bool:
        if isinstance(
            exception, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)
        ):
            return True
        if isinstance(exception, httpx.HTTPStatusError):
            return exception.response.status_code in {408, 429, 500, 502, 503, 504}
        return False

    async def execute(self, method: str, endpoint: str, **kwargs) -> Any:
        retry_count = 0
        while retry_count <= self.max_retries:
            try:
                return await self._request(method, endpoint, **kwargs)
            except Exception as exc:  # noqa: BLE001
                if retry_count < self.max_retries and self._should_retry(exc):
                    retry_count += 1
                    logger.warning(
                        f"{method} {endpoint} failed, retry {retry_count} in {self.retry_delay}s"
                    )
                    await asyncio.sleep(self.retry_delay)
                    continue
                raise

    async def get_server_config(self) -> Optional[Dict]:
        return await self.execute("POST", "/config/server-base")

    def get_server_config_sync(self) -> Optional[Dict]:
        """Sync pull for gRPC/HTTP reload threads (avoids nested event loops)."""
        if not self.enabled:
            return None
        with httpx.Client(
            base_url=self.config.get("url"),
            headers={
                "User-Agent": f"XiaozhiMicroAdmin/2.0-sync (PID:{os.getpid()})",
                "Accept": "application/json",
                "Authorization": "Bearer " + self._secret,
            },
            timeout=self.config.get("timeout", 30),
            trust_env=False,
        ) as client:
            response = client.post("/config/server-base")
            response.raise_for_status()
            result = response.json()
            if result.get("code") != 0:
                raise Exception(f"API返回错误: {result.get('msg', '未知错误')}")
            return result.get("data")

    async def get_agent_models(
        self, mac_address: str, client_id: str, selected_module: Dict
    ) -> Optional[Dict]:
        return await self.execute(
            "POST",
            "/config/agent-models",
            json={
                "macAddress": mac_address,
                "clientId": client_id,
                "selectedModule": selected_module,
            },
        )

    def get_agent_models_sync(
        self, mac_address: str, client_id: str, selected_module: Dict
    ) -> Optional[Dict]:
        """Sync pull for WS connect / gRPC threads."""
        if not self.enabled:
            return None
        with httpx.Client(
            base_url=self.config.get("url"),
            headers={
                "User-Agent": f"XiaozhiMicroAdmin/2.0-sync (PID:{os.getpid()})",
                "Accept": "application/json",
                "Authorization": "Bearer " + self._secret,
            },
            timeout=self.config.get("timeout", 30),
            trust_env=False,
        ) as client:
            response = client.post(
                "/config/agent-models",
                json={
                    "macAddress": mac_address,
                    "clientId": client_id,
                    "selectedModule": selected_module or {},
                },
            )
            response.raise_for_status()
            result = response.json()
            code = result.get("code")
            if code == 10041:
                raise DeviceNotFoundException(result.get("msg"))
            if code == 10042:
                raise DeviceBindException(str(result.get("msg") or ""))
            if code != 0:
                raise Exception(f"API返回错误: {result.get('msg', '未知错误')}")
            return result.get("data")

    async def aclose(self) -> None:
        for client in list(self._clients.values()):
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
