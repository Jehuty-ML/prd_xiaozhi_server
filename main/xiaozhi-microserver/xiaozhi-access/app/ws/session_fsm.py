"""Access-side device session FSM store (micro_service: access owns the machine)."""

from __future__ import annotations

import threading
from typing import Optional

from loguru import logger

from xiaozhi_common.session import (
    COMMAND_TO_EVENT,
    SessionEvent,
    SessionState,
    SessionStateMachine,
    resolve_session_mode,
)


class DeviceSessionStore:
    """Per-client SessionStateMachine registry owned by xiaozhi-access."""

    def __init__(self) -> None:
        self._machines: dict[str, SessionStateMachine] = {}
        self._lock = threading.RLock()
        self._default_mode = "common"

    def set_default_mode(self, mode: str) -> None:
        self._default_mode = resolve_session_mode({"session_state": {"mode": mode}})

    def ensure(
        self, client_id: str, *, session_id: str = "", mode: Optional[str] = None
    ) -> SessionStateMachine:
        with self._lock:
            sm = self._machines.get(client_id)
            if sm is None:
                sm = SessionStateMachine(
                    session_id=session_id or client_id,
                    logger=logger,
                    mode=mode or self._default_mode,
                )
                self._machines[client_id] = sm
            elif session_id:
                sm.set_session_id(session_id)
            return sm

    def get(self, client_id: str) -> Optional[SessionStateMachine]:
        with self._lock:
            return self._machines.get(client_id)

    def remove(self, client_id: str) -> None:
        with self._lock:
            self._machines.pop(client_id, None)

    def get_state(self, client_id: str) -> str:
        sm = self.get(client_id)
        if sm is None:
            return SessionState.IDLE.value
        return sm.state.value

    def get_mode(self, client_id: str) -> str:
        sm = self.get(client_id)
        if sm is None:
            return self._default_mode
        return sm.mode

    def transition(
        self,
        client_id: str,
        event: SessionEvent | str,
        *,
        detail: str = "",
        force: bool = False,
    ) -> bool:
        sm = self.ensure(client_id)
        return sm.transition(event, detail=detail, force=force)

    def apply_command(
        self, client_id: str, command: str, *, detail: str = ""
    ) -> tuple[bool, str]:
        """Map access SendCommand string → validated transition."""
        cmd = (command or "").strip().lower()
        if cmd in ("close_after_chat",):
            return True, cmd
        ev = COMMAND_TO_EVENT.get(cmd)
        if ev is None:
            # Unknown command: not an FSM event
            return True, cmd
        ok = self.transition(client_id, ev, detail=detail or cmd)
        state = self.get_state(client_id)
        return ok, state

    def apply_mode_to_all(self, mode: str, *, only_if_changed: bool = True) -> int:
        """Hot-reload: switch mode on online devices; skip if unchanged."""
        resolved = resolve_session_mode({"session_state": {"mode": mode}})
        changed = 0
        with self._lock:
            self._default_mode = resolved
            for client_id, sm in self._machines.items():
                if only_if_changed and sm.mode == resolved:
                    continue
                if sm.switch_mode(resolved, reset_to_idle=True):
                    changed += 1
                    logger.info(
                        f"session mode applied client={client_id} mode={resolved}"
                    )
        return changed


device_session_store = DeviceSessionStore()
