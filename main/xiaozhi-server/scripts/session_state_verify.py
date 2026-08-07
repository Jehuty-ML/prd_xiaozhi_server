#!/usr/bin/env python3
"""Quick expectation checks for session state (beyond unit tests)."""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from core.utils.session_state import (
    EVENT_ALLOWED_FROM,
    LEGAL_TRANSITIONS,
    SessionEvent,
    SessionState,
    SessionStateMachine,
    sync_legacy_flags,
)


def main():
    log_msgs = []

    class L:
        def info(self, m):
            log_msgs.append(("i", m))

        def warning(self, m):
            log_msgs.append(("w", m))

        def debug(self, m):
            log_msgs.append(("d", m))

    # 1) 日志中的唤醒缓存路径 + LISTENING 可 abort
    sm = SessionStateMachine("s", logger=L())
    assert sm.transition(SessionEvent.DETECT, detail="wakeup_cache")
    assert sm.state == SessionState.DETECT
    assert sm.transition(SessionEvent.TTS_START, detail="wakeup_cache")
    assert sm.state == SessionState.SPEAKING
    assert sm.transition(SessionEvent.TTS_END, detail="clear_speak")
    assert sm.state == SessionState.IDLE
    assert sm.transition(SessionEvent.LISTEN_START)
    assert sm.state == SessionState.LISTENING
    assert sm.transition(SessionEvent.ABORT)
    assert sm.state == SessionState.IDLE
    print("wakeup_cache path + abort from LISTENING: OK")

    # 2) SPEAKING 只能经 TTS_START
    for (frm, ev), to in LEGAL_TRANSITIONS.items():
        if to == SessionState.SPEAKING:
            assert ev == SessionEvent.TTS_START, (frm, ev, to)
    print("SPEAKING only via TTS_START: OK")

    # 3) 超时 / abort 约束
    assert EVENT_ALLOWED_FROM[SessionEvent.IDLE_TIMEOUT] == {SessionState.IDLE}
    assert EVENT_ALLOWED_FROM[SessionEvent.DETECT_TIMEOUT] == {SessionState.DETECT}
    assert SessionEvent.ABORT not in EVENT_ALLOWED_FROM
    print("timeout/abort constraints: OK")

    # 4) 全状态 abort
    for st in SessionState:
        assert (st, SessionEvent.ABORT) in LEGAL_TRANSITIONS
        assert LEGAL_TRANSITIONS[(st, SessionEvent.ABORT)] == SessionState.IDLE
    print("abort from all states: OK")

    # 5) legacy sync
    class C:
        client_is_speaking = True

    c = C()
    sync_legacy_flags(c, SessionState.LISTENING)
    assert c.client_is_speaking is False
    sync_legacy_flags(c, SessionState.SPEAKING)
    assert c.client_is_speaking is True
    print("legacy flag sync: OK")

    # 6) 默认 10s
    from pathlib import Path

    src = Path("core/connection.py").read_text(encoding="utf-8")
    assert "detect_timeout_seconds\", 10)" in src or "detect_timeout_seconds', 10)" in src
    cfg = Path("config.yaml").read_text(encoding="utf-8")
    assert "detect_timeout_seconds: 10" in cfg
    print("default detect_timeout_seconds=10: OK")

    # 7) 非法边仍拒绝
    sm2 = SessionStateMachine("s2", logger=L())
    assert not sm2.transition(SessionEvent.TTS_END)
    assert sm2.state == SessionState.IDLE
    print("illegal TTS_END from IDLE rejected: OK")

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
