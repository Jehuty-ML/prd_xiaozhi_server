"""Session DTO + 主状态机（单体 session_state 对齐）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from xiaozhi_common.session.state_machine import (
    COMMAND_TO_EVENT,
    DEFAULT_SESSION_MODE,
    EVENT_ALLOWED_FROM,
    LEGAL_TRANSITIONS,
    PLAY_ONLY_DENY_TEXT_DEFAULT,
    PLAY_ONLY_SESSION_MODE,
    SESSION_MACHINE_PROFILES,
    SUPPORTED_SESSION_MODES,
    SessionEvent,
    SessionMachineProfile,
    SessionState,
    SessionStateMachine,
    can_continue_play,
    can_continue_think,
    can_send_asr,
    can_start_listen,
    can_start_play,
    can_start_think,
    get_machine_profile,
    get_play_only_deny_text,
    is_play_only_mode,
    parse_session_state,
    resolve_session_mode,
    should_block_user_dialogue_tts,
)

# Re-export peer gates for convenience
from xiaozhi_common.session.device_gates import (  # noqa: E402
    fetch_device_state,
    gate_continue_play,
    gate_continue_think,
    gate_send_asr,
    gate_start_listen,
    gate_start_play,
    gate_start_think,
    send_state_command,
)

__all__ = [
    "COMMAND_TO_EVENT",
    "DEFAULT_SESSION_MODE",
    "EVENT_ALLOWED_FROM",
    "LEGAL_TRANSITIONS",
    "PLAY_ONLY_DENY_TEXT_DEFAULT",
    "PLAY_ONLY_SESSION_MODE",
    "SESSION_MACHINE_PROFILES",
    "SUPPORTED_SESSION_MODES",
    "SessionContext",
    "SessionEvent",
    "SessionMachineProfile",
    "SessionState",
    "SessionStateMachine",
    "can_continue_play",
    "can_continue_think",
    "can_send_asr",
    "can_start_listen",
    "can_start_play",
    "can_start_think",
    "fetch_device_state",
    "gate_continue_play",
    "gate_continue_think",
    "gate_send_asr",
    "gate_start_listen",
    "gate_start_play",
    "gate_start_think",
    "get_machine_profile",
    "get_play_only_deny_text",
    "is_play_only_mode",
    "parse_session_state",
    "resolve_session_mode",
    "send_state_command",
    "should_block_user_dialogue_tts",
]


@dataclass
class SessionContext:
    client_id: str
    session_id: str = ""
    device_id: str = ""
    state: SessionState = SessionState.IDLE
    abort_reason: Optional[str] = None
    extras: dict = field(default_factory=dict)
