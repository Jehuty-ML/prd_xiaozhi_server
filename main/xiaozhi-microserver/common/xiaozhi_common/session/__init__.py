"""Session DTO placeholders for phase-2 abort fan-out."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SessionState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ABORTED = "aborted"


@dataclass
class SessionContext:
    client_id: str
    session_id: str = ""
    device_id: str = ""
    state: SessionState = SessionState.IDLE
    abort_reason: Optional[str] = None
    extras: dict = field(default_factory=dict)
