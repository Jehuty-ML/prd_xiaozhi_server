from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

from fastapi import WebSocket
from loguru import logger


class ConnectionManager:
    """In-memory device WebSocket registry (phase-1)."""

    def __init__(self) -> None:
        self._by_client: dict[str, WebSocket] = {}
        self._by_ws: dict[WebSocket, str] = {}
        self._states: dict[str, str] = {}
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def bind(self, client_id: str, ws: WebSocket) -> None:
        with self._lock:
            self._by_client[client_id] = ws
            self._by_ws[ws] = client_id
            self._states[client_id] = "connected"
        logger.info(f"WS bound client_id={client_id} active={len(self._by_client)}")

    def unbind(self, ws: WebSocket) -> None:
        with self._lock:
            client_id = self._by_ws.pop(ws, None)
            if client_id:
                self._by_client.pop(client_id, None)
                self._states.pop(client_id, None)
                logger.info(f"WS unbound client_id={client_id}")

    def get_client_id(self, ws: WebSocket) -> Optional[str]:
        return self._by_ws.get(ws)

    def get_state(self, client_id: str) -> str:
        return self._states.get(client_id, "unknown")

    def set_state(self, client_id: str, state: str) -> None:
        with self._lock:
            if client_id in self._by_client:
                self._states[client_id] = state

    async def send_bytes(self, client_id: str, data: bytes) -> bool:
        ws = self._by_client.get(client_id)
        if not ws:
            logger.warning(f"No WS for client_id={client_id}")
            return False
        await ws.send_bytes(data)
        return True

    async def send_text(self, client_id: str, text: str) -> bool:
        ws = self._by_client.get(client_id)
        if not ws:
            logger.warning(f"No WS for client_id={client_id}")
            return False
        await ws.send_text(text)
        return True

    def send_bytes_threadsafe(self, client_id: str, data: bytes) -> bool:
        if not self._loop:
            logger.error("Event loop not set on ConnectionManager")
            return False
        fut = asyncio.run_coroutine_threadsafe(self.send_bytes(client_id, data), self._loop)
        try:
            return bool(fut.result(timeout=5))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"send_bytes_threadsafe failed: {exc}")
            return False

    def send_text_threadsafe(self, client_id: str, text: str) -> bool:
        if not self._loop:
            logger.error("Event loop not set on ConnectionManager")
            return False
        fut = asyncio.run_coroutine_threadsafe(self.send_text(client_id, text), self._loop)
        try:
            return bool(fut.result(timeout=5))
        except Exception as exc:  # noqa: BLE001
            logger.error(f"send_text_threadsafe failed: {exc}")
            return False

    @property
    def active_count(self) -> int:
        return len(self._by_client)


# Process-wide singleton used by gRPC servicers and FastAPI routes
connection_manager = ConnectionManager()
