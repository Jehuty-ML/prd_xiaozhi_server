from __future__ import annotations

import asyncio
import threading
from typing import Any, Optional

from fastapi import WebSocket
from loguru import logger

from app.ws.session_fsm import device_session_store
from xiaozhi_common.session import SessionEvent, SessionState


class ConnectionManager:
    """In-memory device WebSocket registry.

    Phase-6: reconnect on the same client_id replaces the previous socket
    without corrupting reverse indexes (old WS is closed and unbound first).
    """

    def __init__(self) -> None:
        self._by_client: dict[str, WebSocket] = {}
        self._by_ws: dict[WebSocket, str] = {}
        self._states: dict[str, str] = {}
        self._meta: dict[str, dict[str, Any]] = {}
        self._session_by_client: dict[str, str] = {}
        self._aliases: dict[str, str] = {}  # secondary id → primary bind_id
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def bind(
        self,
        client_id: str,
        ws: WebSocket,
        *,
        session_id: str = "",
        device_id: str = "",
        alias_id: str = "",
    ) -> Optional[WebSocket]:
        """Bind client_id → ws. Returns previous WS if replaced (caller may close it)."""
        previous: Optional[WebSocket] = None
        with self._lock:
            previous = self._by_client.get(client_id)
            if previous is not None and previous is not ws:
                self._by_ws.pop(previous, None)
            self._by_client[client_id] = ws
            self._by_ws[ws] = client_id
            self._states[client_id] = SessionState.IDLE.value
            meta = self._meta.get(client_id) or {}
            meta.update(
                {
                    "device_id": device_id or client_id,
                    "session_id": session_id,
                }
            )
            self._meta[client_id] = meta
            if session_id:
                self._session_by_client[client_id] = session_id
            # Primary id must never remain as someone else's alias key.
            self._aliases.pop(client_id, None)
            if alias_id and alias_id != client_id:
                # Refuse alias that collides with a live primary connection —
                # otherwise after that primary disconnects, traffic for its id
                # is routed to this socket (wrong-device audio/commands).
                if alias_id in self._by_client:
                    logger.warning(
                        f"WS skip alias={alias_id} — live primary exists "
                        f"(bound client={client_id})"
                    )
                else:
                    self._aliases[alias_id] = client_id
                    meta["client_id"] = alias_id
        device_session_store.ensure(client_id, session_id=session_id or client_id)
        device_session_store.transition(
            client_id, SessionEvent.RESET, detail="bind"
        )
        logger.info(
            f"WS bound client_id={client_id} replaced={previous is not None and previous is not ws} "
            f"active={len(self._by_client)}"
        )
        return previous if previous is not None and previous is not ws else None

    def unbind(self, ws: WebSocket) -> None:
        removed_id: Optional[str] = None
        with self._lock:
            client_id = self._by_ws.pop(ws, None)
            if not client_id:
                return
            # Only clear primary maps if this ws is still the active one
            current = self._by_client.get(client_id)
            if current is not None and current is not ws:
                logger.debug(
                    f"WS unbind ignored stale socket client_id={client_id}"
                )
                return
            self._by_client.pop(client_id, None)
            self._states.pop(client_id, None)
            self._meta.pop(client_id, None)
            self._session_by_client.pop(client_id, None)
            # Drop aliases owned by this primary AND any alias keyed as this id
            # (stale client-id→other mapping that would hijack routing).
            self._aliases.pop(client_id, None)
            stale_aliases = [a for a, p in self._aliases.items() if p == client_id]
            for a in stale_aliases:
                self._aliases.pop(a, None)
            removed_id = client_id
            logger.info(f"WS unbound client_id={client_id}")
        if removed_id:
            device_session_store.remove(removed_id)

    def resolve_client_id(self, client_or_alias: str) -> Optional[str]:
        with self._lock:
            if client_or_alias in self._by_client:
                return client_or_alias
            return self._aliases.get(client_or_alias)

    def get_client_id(self, ws: WebSocket) -> Optional[str]:
        return self._by_ws.get(ws)

    def get_state(self, client_id: str) -> str:
        resolved = self.resolve_client_id(client_id) or client_id
        if resolved in self._by_client or device_session_store.get(resolved):
            return device_session_store.get_state(resolved)
        return self._states.get(resolved, "unknown")

    def set_state(self, client_id: str, state: str) -> bool:
        """Legacy setter → FSM transition. Returns False if illegal."""
        resolved = self.resolve_client_id(client_id) or client_id
        ok, new_state = device_session_store.apply_command(
            resolved, state, detail="set_state"
        )
        with self._lock:
            if resolved in self._by_client or device_session_store.get(resolved):
                self._states[resolved] = new_state if ok else self._states.get(
                    resolved, SessionState.IDLE.value
                )
        return ok

    def transition(
        self, client_id: str, event: SessionEvent | str, *, detail: str = ""
    ) -> bool:
        resolved = self.resolve_client_id(client_id) or client_id
        ok = device_session_store.transition(resolved, event, detail=detail)
        if ok:
            with self._lock:
                self._states[resolved] = device_session_store.get_state(resolved)
        return ok

    def set_meta(self, client_id: str, key: str, value: Any) -> None:
        resolved = self.resolve_client_id(client_id) or client_id
        with self._lock:
            meta = self._meta.setdefault(resolved, {})
            meta[key] = value

    def update_meta(self, client_id: str, **kwargs: Any) -> None:
        resolved = self.resolve_client_id(client_id) or client_id
        with self._lock:
            meta = self._meta.setdefault(resolved, {})
            meta.update(kwargs)

    def get_meta(self, client_id: str) -> dict[str, Any]:
        resolved = self.resolve_client_id(client_id) or client_id
        return dict(self._meta.get(resolved) or {})

    def _lookup_ws(self, client_id: str) -> Optional[WebSocket]:
        resolved = self.resolve_client_id(client_id) or client_id
        return self._by_client.get(resolved)

    async def send_bytes(self, client_id: str, data: bytes) -> bool:
        ws = self._lookup_ws(client_id)
        if not ws:
            logger.warning(f"No WS for client_id={client_id}")
            return False
        await ws.send_bytes(data)
        return True

    async def send_text(self, client_id: str, text: str) -> bool:
        ws = self._lookup_ws(client_id)
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

    def list_client_ids(self) -> list[str]:
        with self._lock:
            return list(self._by_client.keys())


connection_manager = ConnectionManager()
