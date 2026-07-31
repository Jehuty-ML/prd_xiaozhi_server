#!/usr/bin/env python3
"""A/B/C 连接生命周期冒烟与对比测试。

A: 功能回归 — 建连、hello、断开，水位回落
B: 硬上限 — 同 device 超限拒绝；可选全局超限
C: 反复建断 — 观察 active_connections 是否回落、耗时

用法:
  python scripts/ws_lifecycle_smoke.py --url ws://127.0.0.1:8000/xiaozhi/v1/ --label NEW
  python scripts/ws_lifecycle_smoke.py --url ws://127.0.0.1:18000/xiaozhi/v1/ --label OLD --expect-no-limit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import urlopen, Request


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str
    metrics: dict = field(default_factory=dict)


def http_probe(ws_url: str, timeout: float = 3.0) -> str:
    parsed = urlparse(ws_url)
    http_url = f"http://{parsed.hostname}:{parsed.port}/"
    req = Request(http_url, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_active(probe_text: str) -> Optional[int]:
    for line in probe_text.splitlines():
        if line.startswith("active_connections="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


def parse_max(probe_text: str) -> Optional[int]:
    for line in probe_text.splitlines():
        if line.startswith("max_connections="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


def make_headers(device_id: str, client_id: str) -> dict:
    return {
        "device-id": device_id,
        "client-id": client_id,
    }


async def open_ws(url: str, device_id: str, client_id: str):
    import websockets

    return await websockets.connect(
        url,
        additional_headers=make_headers(device_id, client_id),
        open_timeout=10,
        close_timeout=3,
        ping_interval=None,
    )


async def send_hello(ws, device_id: str) -> Optional[dict]:
    hello = {
        "type": "hello",
        "device_id": device_id,
        "device_name": "lifecycle-smoke",
        "device_mac": device_id,
        "features": {"mcp": False},
    }
    await ws.send(json.dumps(hello, ensure_ascii=False))
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=8)
    except asyncio.TimeoutError:
        return None
    if isinstance(raw, bytes):
        return {"_binary": True, "size": len(raw)}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw[:200]}


async def try_open_session(url: str, device_id: str, client_id: str) -> Tuple[Optional[object], Optional[str]]:
    """尝试建立可用会话。

    服务端可能在握手后立刻以 1013 关闭（硬上限）。
    返回 (ws, reject_reason)；成功时 reject_reason 为 None。
    """
    try:
        ws = await open_ws(url, device_id, client_id)
    except Exception as e:
        return None, f"connect_failed: {type(e).__name__}: {e}"

    # 给服务端一点时间做 auth/限流并可能立刻 close
    await asyncio.sleep(0.5)
    close_code = getattr(ws, "close_code", None)
    if close_code is not None:
        reason = getattr(ws, "close_reason", "") or ""
        try:
            await ws.close()
        except Exception:
            pass
        return None, f"closed_after_handshake code={close_code} reason={reason}"

    # 再探活：已关闭的连接 send/ping 会失败
    try:
        await asyncio.wait_for(ws.ping(), timeout=1.0)
    except Exception as e:
        code = getattr(ws, "close_code", None)
        reason = getattr(ws, "close_reason", "") or str(e)
        try:
            await ws.close()
        except Exception:
            pass
        return None, f"ping_failed code={code} reason={reason}"

    return ws, None


async def case_a(url: str) -> CaseResult:
    device_id = f"AA:SM:0A:{uuid.uuid4().hex[:2].upper()}:{uuid.uuid4().hex[:2].upper()}:{uuid.uuid4().hex[:2].upper()}"
    client_id = f"smoke_a_{uuid.uuid4().hex[:8]}"
    before = http_probe(url)
    active_before = parse_active(before)

    ws, reject = await try_open_session(url, device_id, client_id)
    if ws is None:
        return CaseResult(
            "A_功能回归_hello断开",
            False,
            f"无法建连: {reject}",
            {"active_before": active_before},
        )
    reply = await send_hello(ws, device_id)
    mid = http_probe(url)
    active_mid = parse_active(mid)
    await ws.close()
    await asyncio.sleep(1.5)
    after = http_probe(url)
    active_after = parse_active(after)

    ok_hello = reply is not None
    # 有水位字段时校验；旧版没有则只校验 hello
    ok_water = True
    detail_parts = [f"hello_reply={reply is not None}"]
    if active_before is not None and active_mid is not None and active_after is not None:
        ok_water = active_mid >= active_before and active_after <= active_mid
        # 理想：断开后回到 before（允许短暂延迟 ±0，多等一次）
        if active_after > active_before:
            await asyncio.sleep(2.0)
            after2 = http_probe(url)
            active_after = parse_active(after2)
            ok_water = active_after is not None and active_after <= active_before
        detail_parts.append(
            f"active before/mid/after={active_before}/{active_mid}/{active_after}"
        )
    else:
        detail_parts.append("active_connections 字段不可用(可能是旧版)")

    passed = ok_hello and ok_water
    return CaseResult(
        "A_功能回归_hello断开",
        passed,
        "; ".join(detail_parts),
        {
            "active_before": active_before,
            "active_mid": active_mid,
            "active_after": active_after,
            "hello_ok": ok_hello,
        },
    )


async def case_b_device_limit(
    url: str, per_device_limit: int, expect_no_limit: bool
) -> CaseResult:
    device_id = f"BB:SM:0B:01:02:03"
    client_id_base = f"smoke_b_{uuid.uuid4().hex[:6]}"
    sockets = []
    rejected = False
    reject_detail = ""

    try:
        for i in range(per_device_limit + 1):
            client_id = f"{client_id_base}_{i}"
            ws, reject = await try_open_session(url, device_id, client_id)
            if ws is None:
                rejected = True
                reject_detail = reject or ""
                break
            sockets.append(ws)
    finally:
        for ws in sockets:
            try:
                await ws.close()
            except Exception:
                pass
        await asyncio.sleep(1.5)

    opened = len(sockets)
    if expect_no_limit:
        # 旧版：不应拒绝，应能开到 limit+1
        passed = not rejected and opened == per_device_limit + 1
        detail = (
            f"expect_no_limit; opened={opened}/{per_device_limit + 1}; "
            f"rejected={rejected} {reject_detail}"
        )
    else:
        passed = rejected and opened == per_device_limit
        detail = (
            f"per_device={per_device_limit}; opened={opened}; "
            f"rejected={rejected} {reject_detail}"
        )

    return CaseResult(
        "B_同设备连接上限",
        passed,
        detail,
        {"opened": opened, "rejected": rejected, "per_device_limit": per_device_limit},
    )


async def case_b_global_limit(
    url: str, global_limit: int, expect_no_limit: bool
) -> CaseResult:
    """用不同 device-id 打满全局上限。仅在测试环境把 max_connections 调很小时使用。"""
    sockets = []
    rejected = False
    reject_detail = ""
    try:
        for i in range(global_limit + 1):
            device_id = f"CC:SM:0C:{i:02X}:00:01"
            client_id = f"smoke_bg_{uuid.uuid4().hex[:6]}_{i}"
            ws, reject = await try_open_session(url, device_id, client_id)
            if ws is None:
                rejected = True
                reject_detail = reject or ""
                break
            sockets.append(ws)
    finally:
        for ws in sockets:
            try:
                await ws.close()
            except Exception:
                pass
        await asyncio.sleep(1.5)

    opened = len(sockets)
    if expect_no_limit:
        passed = not rejected and opened == global_limit + 1
        detail = (
            f"expect_no_limit; opened={opened}/{global_limit + 1}; "
            f"rejected={rejected} {reject_detail}"
        )
    else:
        passed = rejected and opened == global_limit
        detail = (
            f"global={global_limit}; opened={opened}; "
            f"rejected={rejected} {reject_detail}"
        )

    return CaseResult(
        "B_全局连接上限",
        passed,
        detail,
        {"opened": opened, "rejected": rejected, "global_limit": global_limit},
    )


async def case_c_churn(url: str, rounds: int) -> CaseResult:
    device_prefix = f"DD:SM:0D:{uuid.uuid4().hex[:2].upper()}"
    before = http_probe(url)
    active_before = parse_active(before)
    durations: List[float] = []
    failures = 0

    for i in range(rounds):
        device_id = f"{device_prefix}:{i:02X}:EE"
        client_id = f"smoke_c_{i}_{uuid.uuid4().hex[:4]}"
        t0 = time.perf_counter()
        try:
            ws, reject = await try_open_session(url, device_id, client_id)
            if ws is None:
                failures += 1
                continue
            await send_hello(ws, device_id)
            await ws.close()
            durations.append(time.perf_counter() - t0)
        except Exception:
            failures += 1
        await asyncio.sleep(0.05)

    # 等清理
    await asyncio.sleep(2.5)
    after = http_probe(url)
    active_after = parse_active(after)
    if active_before is not None and active_after is not None and active_after > active_before:
        await asyncio.sleep(3.0)
        after = http_probe(url)
        active_after = parse_active(after)

    leak = None
    if active_before is not None and active_after is not None:
        leak = active_after - active_before

    p50 = statistics.median(durations) if durations else None
    p95 = (
        statistics.quantiles(durations, n=20)[18]
        if len(durations) >= 20
        else (max(durations) if durations else None)
    )

    # 通过标准：失败率低；有水位时泄漏 <= 0（允许 0）
    passed = failures <= max(1, rounds // 20)
    if leak is not None:
        passed = passed and leak <= 0

    detail = (
        f"rounds={rounds}; failures={failures}; "
        f"active before/after={active_before}/{active_after}; leak={leak}; "
        f"p50={p50:.3f}s p95={p95:.3f}s" if p50 is not None and p95 is not None
        else f"rounds={rounds}; failures={failures}; "
        f"active before/after={active_before}/{active_after}; leak={leak}"
    )

    return CaseResult(
        "C_反复建断连",
        passed,
        detail,
        {
            "rounds": rounds,
            "failures": failures,
            "active_before": active_before,
            "active_after": active_after,
            "leak": leak,
            "p50_s": p50,
            "p95_s": p95,
        },
    )


def print_report(label: str, url: str, results: List[CaseResult]) -> int:
    print("=" * 72)
    print(f"LABEL={label}")
    print(f"URL={url}")
    try:
        probe = http_probe(url)
        print("--- HTTP probe ---")
        print(probe.rstrip())
        print(f"parsed active={parse_active(probe)} max={parse_max(probe)}")
    except Exception as e:
        print(f"HTTP probe failed: {e}")
    print("--- Cases ---")
    failed = 0
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        if not r.passed:
            failed += 1
        print(f"[{status}] {r.name}: {r.detail}")
    print(f"SUMMARY {label}: {len(results) - failed}/{len(results)} passed")
    print("=" * 72)
    return failed


async def run_all(args) -> int:
    results: List[CaseResult] = []
    results.append(await case_a(args.url))
    results.append(
        await case_b_device_limit(
            args.url, args.per_device_limit, args.expect_no_limit
        )
    )
    if args.global_limit is not None:
        results.append(
            await case_b_global_limit(
                args.url, args.global_limit, args.expect_no_limit
            )
        )
    results.append(await case_c_churn(args.url, args.rounds))
    return print_report(args.label, args.url, results)


def main():
    parser = argparse.ArgumentParser(description="WS lifecycle A/B/C smoke test")
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8000/xiaozhi/v1/",
        help="WebSocket URL",
    )
    parser.add_argument("--label", default="NEW", help="Report label")
    parser.add_argument(
        "--per-device-limit",
        type=int,
        default=2,
        help="Expected max_connections_per_device",
    )
    parser.add_argument(
        "--global-limit",
        type=int,
        default=None,
        help="If set, also test global max_connections (use small value in test env)",
    )
    parser.add_argument("--rounds", type=int, default=30, help="Churn rounds for C")
    parser.add_argument(
        "--expect-no-limit",
        action="store_true",
        help="Old build: expect connections are NOT rejected by limits",
    )
    args = parser.parse_args()
    try:
        failed = asyncio.run(run_all(args))
    except KeyboardInterrupt:
        return 130
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
