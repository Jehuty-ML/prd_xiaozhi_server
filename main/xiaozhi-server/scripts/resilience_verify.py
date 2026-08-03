#!/usr/bin/env python3
"""韧性模块验证：配置加载、预算、熔断、预置音、过载检测、指标端点。"""

from __future__ import annotations

import asyncio
import sys
import time
import urllib.request

sys.path.insert(0, ".")


def fetch(url: str, timeout: float = 5.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


async def verify_config():
    from config.config_loader import load_config
    from core.utils.resilience import (
        UpstreamError,
        UpstreamKind,
        RoundBudget,
        get_resilience_settings,
        resolve_tts_fallback_audio,
        get_fallback_text,
        get_circuit,
        get_provider_policy,
        next_with_timeout,
        check_system_overload,
    )

    cfg = await load_config()
    s = get_resilience_settings(cfg)
    errors = []

    def ok(name, cond, detail=""):
        status = "PASS" if cond else "FAIL"
        print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
        if not cond:
            errors.append(name)

    print("=== 1. 配置 ===")
    ok("enabled", s.enabled is True, f"enabled={s.enabled}")
    ok("ttfb>=15", s.llm_ttfb_deadline_seconds >= 15, f"{s.llm_ttfb_deadline_seconds}s")
    ok("round>=60", s.round_deadline_seconds >= 60, f"{s.round_deadline_seconds}s")
    ok("ttfb < round", s.llm_ttfb_deadline_seconds < s.round_deadline_seconds)
    ok("asr_timeout", s.asr_timeout_seconds > 0, f"{s.asr_timeout_seconds}s")
    ok("llm_timeout", s.llm_timeout_seconds >= 60, f"{s.llm_timeout_seconds}s")
    ok("overload_enabled", s.overload_enabled is True)
    ok("use_tts_fallback", s.use_tts_fallback_on_degrade is True)
    path = resolve_tts_fallback_audio(cfg)
    ok("tts_fallback_file", bool(path), str(path))
    ok("providers_keys", set(s.providers or {}).issuperset({"asr", "llm", "tts"}))

    print("=== 2. RoundBudget ===")
    b = RoundBudget.start(s)
    ok("ttfb_remaining", (b.ttfb_remaining() or 0) > 0)
    b.mark_first_token()
    ok("ttfb_cleared_after_first", b.ttfb_remaining() is None)
    short = RoundBudget(
        started_at=time.monotonic() - 1,
        llm_ttfb_deadline_seconds=0.01,
        round_deadline_seconds=100,
    )
    try:
        short.ensure_ttfb()
        ok("ttfb_timeout_raises", False)
    except UpstreamError as e:
        ok("ttfb_timeout_raises", e.kind == UpstreamKind.TIMEOUT, str(e))

    expired = RoundBudget(
        started_at=time.monotonic() - 200,
        llm_ttfb_deadline_seconds=25,
        round_deadline_seconds=120,
    )
    try:
        expired.ensure_round()
        ok("round_timeout_raises", False)
    except UpstreamError as e:
        ok("round_timeout_raises", e.kind == UpstreamKind.TIMEOUT)

    print("=== 3. 熔断（含异步上下文打日志） ===")
    c = get_circuit("verify:smoke", s)
    c.failure_threshold = 2
    c.record_success()
    ok("circuit_closed", c.state() == "closed", c.state())
    c.record_failure()
    c.record_failure()
    ok("circuit_open", c.state() == "open", c.state())
    ok("circuit_not_allow", c.allow() is False)
    c.record_success()
    ok("circuit_recover", c.state() == "closed")

    print("=== 4. 话术 / 策略 ===")
    overload_txt = get_fallback_text(cfg, "overload", UpstreamKind.OVERLOAD)
    ok("overload_phrase", len(overload_txt) > 0, overload_txt[:40])
    asr_txt = get_fallback_text(cfg, "asr", UpstreamKind.EMPTY)
    ok("asr_empty_phrase", len(asr_txt) > 0, asr_txt[:40])
    policy = get_provider_policy(cfg, "llm", "default")
    ok("provider_policy", "timeout_seconds" in policy or "max_retries" in policy, str(policy))

    print("=== 5. next_with_timeout ===")
    assert next_with_timeout(iter([42]), 1.0) == 42
    ok("next_with_timeout", True)

    print("=== 6. 过载检测（无假 server 应为 None） ===")

    class _Dummy:
        config = cfg
        server = None
        tts = None
        report_queue = None

    reason = check_system_overload(_Dummy())
    ok("overload_idle_none", reason is None, str(reason))

    return errors


def verify_metrics():
    print("=== 7. 线上 /metrics ===")
    errors = []
    text = fetch("http://127.0.0.1:8003/metrics")
    for name in (
        "xiaozhi_ws_active_connections",
        "xiaozhi_circuit_state",
        "xiaozhi_degraded_total",
        "xiaozhi_overload_shed_total",
        "xiaozhi_provider_requests_total",
        "xiaozhi_queue_depth",
    ):
        hit = name in text
        print(f"  [{'PASS' if hit else 'FAIL'}] metric {name}")
        if not hit:
            errors.append(name)
    probe = fetch("http://127.0.0.1:8000/")
    has_active = "active_connections=" in probe
    print(f"  [{'PASS' if has_active else 'FAIL'}] ws probe water level")
    if not has_active:
        errors.append("ws_probe")
    else:
        print(f"    {probe.strip().replace(chr(10), ' | ')}")
    return errors


async def main():
    e1 = await verify_config()
    e2 = verify_metrics()
    all_err = e1 + e2
    print()
    if all_err:
        print(f"FAILED ({len(all_err)}): {all_err}")
        return 1
    print("ALL VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
