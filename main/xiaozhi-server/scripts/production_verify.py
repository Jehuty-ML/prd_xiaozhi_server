#!/usr/bin/env python3
"""生产加固验证：策略单测入口 + /health /ready /metrics 探活。

用法:
  # 离线（不依赖运行中服务）+ 若 8003 可达则顺带探活
  python scripts/production_verify.py

  # 仅跑 unittest
  python scripts/production_verify.py --unit-only

  # 仅线上探活（需已重启含 /health 的 xiaozhi-server）
  python scripts/production_verify.py --online-only

  # 自定义地址
  python scripts/production_verify.py --http-base http://127.0.0.1:8003 --ws-probe http://127.0.0.1:8000/
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_ROOT = os.path.dirname(_SCRIPT_DIR)
if _SERVER_ROOT not in sys.path:
    sys.path.insert(0, _SERVER_ROOT)


def _ok(name: str, cond: bool, detail: str = "", errors: Optional[List[str]] = None) -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    if not cond and errors is not None:
        errors.append(name)


def fetch(
    url: str, timeout: float = 5.0
) -> Tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return int(e.code), body
    except Exception as e:
        return 0, str(e)


def verify_policy_offline() -> List[str]:
    """不启服务：白名单 / 环境策略。"""
    from core.utils.runtime_env import (
        resolve_auth_enabled,
        resolve_whitelist_bypass_allowed,
        should_bypass_token_for_device,
    )
    from core.utils.health import (
        build_liveness_payload,
        build_readiness_payload,
        get_health_paths,
        health_state,
    )
    import asyncio

    print("=== 1. 离线策略 ===")
    errors: List[str] = []

    prod = {"server": {"environment": "production", "auth": {"enabled": True}}}
    _ok(
        "prod_auth_forced",
        resolve_auth_enabled(prod) is True,
        errors=errors,
    )
    _ok(
        "prod_no_whitelist_bypass",
        resolve_whitelist_bypass_allowed(prod) is False,
        errors=errors,
    )
    _ok(
        "prod_whitelist_still_needs_token",
        should_bypass_token_for_device(prod, "aa:bb", {"aa:bb"}) is False,
        errors=errors,
    )

    dev = {
        "server": {
            "environment": "development",
            "auth": {"enabled": True, "allowed_devices": ["aa:bb"]},
        }
    }
    _ok(
        "dev_whitelist_bypass",
        should_bypass_token_for_device(dev, "aa:bb", {"aa:bb"}) is True,
        errors=errors,
    )

    live, ready = get_health_paths({})
    _ok("paths", live == "/health" and ready == "/ready", f"{live},{ready}", errors)

    health_state.config = {"server": {"environment": "development", "registry": {"enabled": False}}}
    health_state.ws_server = None
    health_state.dialogue_registrar = None
    health_state.http_started = False
    payload = build_liveness_payload(health_state.config)
    _ok("liveness_payload", payload.get("status") == "ok", errors=errors)

    ready_ok, ready_payload = asyncio.run(build_readiness_payload(health_state.config))
    _ok(
        "readiness_not_ready_before_bind",
        ready_ok is False and ready_payload.get("status") == "not_ready",
        errors=errors,
    )
    return errors


def verify_online(http_base: str, ws_probe: str) -> List[str]:
    print("=== 2. 线上探活 ===")
    errors: List[str] = []
    base = http_base.rstrip("/")

    code, body = fetch(f"{base}/health")
    if code == 404:
        _ok(
            "health_endpoint",
            False,
            "404 — 请重启 xiaozhi-server 以加载 /health",
            errors,
        )
    else:
        try:
            data = json.loads(body) if body.startswith("{") else {}
        except json.JSONDecodeError:
            data = {}
        _ok(
            "health_endpoint",
            code == 200 and data.get("status") == "ok",
            f"HTTP {code} {body[:120]}",
            errors,
        )

    code, body = fetch(f"{base}/ready")
    if code == 404:
        _ok(
            "ready_endpoint",
            False,
            "404 — 请重启 xiaozhi-server 以加载 /ready",
            errors,
        )
    else:
        try:
            data = json.loads(body) if body.startswith("{") else {}
        except json.JSONDecodeError:
            data = {}
        # 503 也算端点可用，但未就绪要标 FAIL（默认期望 ready）
        endpoint_ok = code in (200, 503) and "status" in data
        _ok(
            "ready_endpoint",
            endpoint_ok,
            f"HTTP {code} status={data.get('status')}",
            errors,
        )
        if endpoint_ok:
            auth = (data.get("checks") or {}).get("auth") or {}
            _ok(
                "ready_has_auth_checks",
                "enabled" in auth and "whitelist_bypass" in auth,
                str(auth),
                errors,
            )
            if code == 200:
                _ok("ready_status_ready", data.get("status") == "ready", errors=errors)
            else:
                print(f"    (info) not ready: {json.dumps(data.get('checks'), ensure_ascii=False)[:200]}")

    code, body = fetch(f"{base}/metrics")
    metric_names = (
        "xiaozhi_ws_active_connections",
        "xiaozhi_circuit_state",
        "xiaozhi_degraded_total",
        "xiaozhi_overload_shed_total",
    )
    if code != 200:
        _ok("metrics_endpoint", False, f"HTTP {code}", errors)
    else:
        _ok("metrics_endpoint", True, errors=errors)
        for name in metric_names:
            _ok(f"metric_{name}", name in body, errors=errors)

    code, body = fetch(ws_probe)
    _ok(
        "ws_probe_water_level",
        code == 200 and "active_connections=" in body,
        body.strip().replace("\n", " | ")[:160] if code == 200 else f"HTTP {code}",
        errors,
    )
    return errors


def run_unit_tests() -> int:
    print("=== 0. unittest ===")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-q"],
        cwd=_SERVER_ROOT,
        env=env,
    )
    print(f"  [{'PASS' if proc.returncode == 0 else 'FAIL'}] unittest exit={proc.returncode}")
    return proc.returncode


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="生产加固验证（单测 + 探活）")
    p.add_argument("--http-base", default="http://127.0.0.1:8003")
    p.add_argument("--ws-probe", default="http://127.0.0.1:8000/")
    p.add_argument("--unit-only", action="store_true")
    p.add_argument("--online-only", action="store_true")
    p.add_argument("--skip-unit", action="store_true", help="跳过 unittest，只做策略+探活")
    return p


def main() -> int:
    args = build_argparser().parse_args()
    rc = 0
    all_err: List[str] = []

    if args.online_only:
        all_err.extend(verify_online(args.http_base, args.ws_probe))
    elif args.unit_only:
        rc = run_unit_tests()
    else:
        if not args.skip_unit:
            rc = run_unit_tests()
        all_err.extend(verify_policy_offline())
        all_err.extend(verify_online(args.http_base, args.ws_probe))

    print()
    if rc != 0 or all_err:
        print(f"FAILED unit_rc={rc} checks={all_err}")
        return 1
    print("ALL VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
