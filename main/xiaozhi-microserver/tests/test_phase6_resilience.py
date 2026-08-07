"""Phase-6: resilience, connection manager, admin auth gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MICRO_ROOT = Path(__file__).resolve().parents[1]
ACCESS_ROOT = MICRO_ROOT / "xiaozhi-access"
ADMIN_ROOT = MICRO_ROOT / "xiaozhi-model-admin"
COMMON = MICRO_ROOT / "common"


def _clear_app() -> None:
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]


def _use_paths(*roots: Path) -> None:
    _clear_app()
    paths = [str(Path(r).resolve()) for r in roots] + [
        str(COMMON.resolve()),
        str((COMMON / "generated").resolve()),
    ]
    service_markers = (
        "xiaozhi-access",
        "xiaozhi-model-admin",
        "xiaozhi-agent",
        "xiaozhi-audio-speaker",
        "xiaozhi-audio-preprocess",
        "xiaozhi-audio-receiver",
    )
    kept: list[str] = []
    for s in sys.path:
        norm = s.replace("\\", "/")
        if any(m in norm for m in service_markers):
            continue
        if s in paths:
            continue
        kept.append(s)
    sys.path[:] = paths + kept


def test_circuit_opens_after_threshold():
    _use_paths()
    from xiaozhi_common.resilience import (
        UpstreamError,
        UpstreamKind,
        call_with_resilience,
        reset_circuits_for_tests,
    )

    reset_circuits_for_tests()
    cfg = {
        "server": {
            "resilience": {
                "enabled": True,
                "max_retries": 0,
                "circuit_failure_threshold": 2,
                "circuit_open_seconds": 60,
            }
        }
    }

    def boom():
        raise TimeoutError("deadline")

    for _ in range(2):
        with pytest.raises(UpstreamError) as ei:
            call_with_resilience("asr", boom, config=cfg, provider="t")
        assert ei.value.kind == UpstreamKind.TIMEOUT

    with pytest.raises(UpstreamError) as ei:
        call_with_resilience("asr", boom, config=cfg, provider="t")
    assert ei.value.kind == UpstreamKind.CIRCUIT_OPEN


def test_retry_then_success():
    _use_paths()
    from xiaozhi_common.resilience import call_with_resilience, reset_circuits_for_tests

    reset_circuits_for_tests()
    cfg = {
        "server": {
            "resilience": {
                "enabled": True,
                "max_retries": 2,
                "retry_delay_seconds": 0.01,
                "circuit_failure_threshold": 10,
            }
        }
    }
    box = {"n": 0}

    def flaky():
        box["n"] += 1
        if box["n"] < 2:
            raise ConnectionError("unavailable")
        return "ok"

    assert call_with_resilience("grpc", flaky, config=cfg, provider="x") == "ok"
    assert box["n"] == 2


def test_connection_manager_replace_does_not_corrupt():
    _use_paths(ACCESS_ROOT)
    from app.ws.manager import ConnectionManager

    class FakeWS:
        def __init__(self, name):
            self.name = name

        def __repr__(self):
            return f"FakeWS({self.name})"

    mgr = ConnectionManager()
    a = FakeWS("a")
    b = FakeWS("b")
    prev = mgr.bind("dev-1", a, session_id="s1", device_id="dev-1", alias_id="c1")
    assert prev is None
    assert mgr.active_count == 1
    prev = mgr.bind("dev-1", b, session_id="s2", device_id="dev-1", alias_id="c1")
    assert prev is a
    assert mgr.active_count == 1
    assert mgr.get_client_id(b) == "dev-1"
    assert mgr.get_client_id(a) is None
    mgr.unbind(a)
    assert mgr.active_count == 1
    assert mgr.resolve_client_id("c1") == "dev-1"
    mgr.unbind(b)
    assert mgr.active_count == 0


def test_admin_auth_required_in_production():
    _use_paths(ADMIN_ROOT)
    from app.api.admin_auth import admin_auth_required, resolve_admin_token

    assert not admin_auth_required({"server": {"environment": "development"}})
    assert admin_auth_required({"server": {"environment": "production"}})
    assert admin_auth_required(
        {"server": {"environment": "development", "admin": {"require_auth": True}}}
    )
    tok = resolve_admin_token(
        {"server": {"admin": {"token": "abc"}, "auth_key": "ignored"}}
    )
    assert tok == "abc"
