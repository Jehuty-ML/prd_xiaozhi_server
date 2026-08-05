#!/usr/bin/env python3
"""xiaozhi-server 单机容量压测（可绑 N 核，默认压测场景按 1 核）。

两类指标（对应生产容量账本）：
  1) idle  — 纯挂机连接：内存 / FD(句柄) / 空闲 CPU，爬坡找上限
  2) chat  — 同时「开口」对话：listen detect → LLM+TTS（跳过 ASR，可稳定复现）
  3) asr   — 同时开口识别+对话：manual 听音 → 发 Opus → stop → ASR+LLM+TTS
             （需本机 opus 库；可用 --audio-wav 指定 16k 单声道 wav）

绑核（限制服务端只用 N 核）：
  # 先启动 app.py，再对其 PID 绑 1 核后压测（max_connections=2000）
  python scripts/capacity_bench.py --pin-cores 1 --server-pid <PID> --mode idle --idle-target 2000

  # 或自动查找监听 8000 的 python 进程
  python scripts/capacity_bench.py --pin-cores 1 --auto-find-server --mode idle --idle-target 2000

示例：
  python scripts/capacity_bench.py --mode idle --target 2000 --step 100 --hold-seconds 8
  python scripts/capacity_bench.py --mode chat --concurrency 30 --prompt "今天天气怎么样"
  python scripts/capacity_bench.py --mode asr --concurrency 20 --audio-wav path/to/16k_mono.wav
  python scripts/capacity_bench.py --mode both --idle-target 2000 --chat-concurrency 10 --pin-cores 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import struct
import sys
import time
import uuid
import wave
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_ROOT = os.path.dirname(_SCRIPT_DIR)
if _SERVER_ROOT not in sys.path:
    sys.path.insert(0, _SERVER_ROOT)

import importlib.util


def _load_smoke():
    path = os.path.join(_SCRIPT_DIR, "ws_lifecycle_smoke.py")
    name = "ws_lifecycle_smoke"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_smoke = _load_smoke()
SmokeAuth = _smoke.SmokeAuth
http_probe = _smoke.http_probe
parse_active = _smoke.parse_active
parse_max = _smoke.parse_max
resolve_smoke_auth = _smoke.resolve_smoke_auth



@dataclass
class Sample:
    t: float
    n_clients: int
    rss_mb: Optional[float]
    cpu_percent: Optional[float]
    num_fds: Optional[int]
    active_connections: Optional[int]


@dataclass
class BenchResult:
    mode: str
    ok: bool
    detail: str
    samples: List[Sample] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


def _mac(i: int) -> str:
    return f"CA:P0:{(i >> 16) & 0xFF:02X}:{(i >> 8) & 0xFF:02X}:{i & 0xFF:02X}:{(i * 7) & 0xFF:02X}"


def find_server_pid(port: int = 8000) -> Optional[int]:
    try:
        import psutil
    except ImportError:
        return None
    for c in psutil.net_connections(kind="inet"):
        if c.laddr and c.laddr.port == port and c.status == "LISTEN" and c.pid:
            try:
                p = psutil.Process(c.pid)
                name = (p.name() or "").lower()
                cmd = " ".join(p.cmdline()).lower()
                if "python" in name or "app.py" in cmd or "xiaozhi" in cmd:
                    return c.pid
            except (psutil.Error, TypeError):
                continue
    return None


def pin_process_cores(pid: int, n_cores: int) -> List[int]:
    """将进程 CPU 亲和性限制到前 n_cores 个逻辑核。"""
    import psutil

    p = psutil.Process(pid)
    all_cores = list(range(psutil.cpu_count() or n_cores))
    if n_cores < 1:
        raise ValueError("n_cores must be >= 1")
    chosen = all_cores[: min(n_cores, len(all_cores))]
    p.cpu_affinity(chosen)
    return chosen


# 复用同一 Process 实例：cpu_percent(interval=None) 依赖「相对上次」的增量。
_PROC_CACHE: Dict[int, Any] = {}


def _cached_process(pid: int):
    import psutil

    p = _PROC_CACHE.get(pid)
    if p is None or not p.is_running() or p.pid != pid:
        p = psutil.Process(pid)
        # 第一次调用建立基线，固定返回 0.0，丢弃
        p.cpu_percent(interval=None)
        _PROC_CACHE[pid] = p
    return p


def sample_process(pid: Optional[int]) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    """采样 RSS / CPU / FD。

    CPU 含义（psutil）：100% ≈ 占满 1 个逻辑核；多线程可 >100%。
    绑 N 核时，理论峰值约 N*100%。
    """
    if not pid:
        return None, None, None
    try:
        p = _cached_process(pid)
        with p.oneshot():
            rss = p.memory_info().rss / (1024 * 1024)
            cpu = p.cpu_percent(interval=None)
            try:
                fds = p.num_handles()  # Windows
            except Exception:
                try:
                    fds = p.num_fds()  # Unix
                except Exception:
                    fds = None
        return rss, cpu, fds
    except Exception:
        _PROC_CACHE.pop(pid, None)
        return None, None, None


async def sample_cpu_during(pid: Optional[int], stop: asyncio.Event, interval: float = 0.25) -> Dict[str, Any]:
    """突发期间周期性采 CPU，返回 avg/peak/samples。"""
    vals: List[float] = []
    if not pid:
        await stop.wait()
        return {"avg": None, "peak": None, "n": 0, "samples": vals}
    # 确保缓存进程已预热
    sample_process(pid)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass
        _, cpu, _ = sample_process(pid)
        if cpu is not None:
            vals.append(float(cpu))
    if not vals:
        return {"avg": None, "peak": None, "n": 0, "samples": vals}
    return {
        "avg": sum(vals) / len(vals),
        "peak": max(vals),
        "n": len(vals),
        "samples": vals,
    }


class AuthHolder:
    auth: SmokeAuth = SmokeAuth(enabled=False, source="unset")


_AUTH = AuthHolder()


async def open_ws(url: str, device_id: str, client_id: str):
    import websockets

    return await websockets.connect(
        url,
        additional_headers=_AUTH.auth.headers(device_id, client_id),
        open_timeout=10,
        close_timeout=3,
        ping_interval=20,
        max_size=8 * 1024 * 1024,
    )


async def send_hello(ws, device_id: str) -> Optional[dict]:
    hello = {
        "type": "hello",
        "device_id": device_id,
        "device_name": "capacity-bench",
        "device_mac": device_id,
        "features": {"mcp": False},
    }
    await ws.send(json.dumps(hello, ensure_ascii=False))
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=8)
    except asyncio.TimeoutError:
        return None
    if isinstance(raw, bytes):
        return {"_binary": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": str(raw)[:120]}


async def open_session(url: str, idx: int) -> Tuple[Optional[Any], Optional[str], str]:
    device_id = _mac(idx)
    client_id = f"cap_{idx}_{uuid.uuid4().hex[:6]}"
    try:
        ws = await open_ws(url, device_id, client_id)
    except Exception as e:
        return None, f"connect:{type(e).__name__}:{e}", device_id
    await asyncio.sleep(0.15)
    if getattr(ws, "close_code", None) is not None:
        code = ws.close_code
        reason = getattr(ws, "close_reason", "") or ""
        try:
            await ws.close()
        except Exception:
            pass
        return None, f"closed_after_handshake code={code} reason={reason}", device_id
    reply = await send_hello(ws, device_id)
    if reply is None:
        try:
            await ws.close()
        except Exception:
            pass
        return None, "hello_timeout", device_id
    return ws, None, device_id


def build_tone_pcm(seconds: float = 1.2, sample_rate: int = 16000, hz: float = 440.0) -> bytes:
    n = int(seconds * sample_rate)
    out = bytearray()
    for i in range(n):
        v = int(12000 * math.sin(2 * math.pi * hz * i / sample_rate))
        out += struct.pack("<h", max(-32767, min(32767, v)))
    return bytes(out)


def load_wav_pcm16_mono_16k(path: str) -> bytes:
    with wave.open(path, "rb") as wf:
        ch = wf.getnchannels()
        sw = wf.getsampwidth()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if sw != 2:
        raise ValueError(f"需要 16-bit PCM wav，当前 sampwidth={sw}")
    if ch == 2:
        # 取左声道
        samples = memoryview(raw).cast("h")
        mono = struct.pack("<%dh" % (len(samples) // 2), *samples[0::2])
        raw = mono
    elif ch != 1:
        raise ValueError(f"不支持 channels={ch}")
    if rate != 16000:
        raise ValueError(f"需要 16kHz wav，当前 rate={rate}（请先重采样）")
    return raw


def pcm_to_opus_frames(pcm: bytes, sample_rate: int = 16000, frame_ms: int = 60) -> List[bytes]:
    import opuslib_next

    frame_samples = sample_rate * frame_ms // 1000
    frame_bytes = frame_samples * 2
    enc = opuslib_next.Encoder(sample_rate, 1, opuslib_next.APPLICATION_VOIP)
    frames: List[bytes] = []
    for off in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        chunk = pcm[off : off + frame_bytes]
        frames.append(enc.encode(chunk, frame_samples))
    if not frames:
        raise ValueError("音频太短，无法编码 Opus 帧")
    return frames


async def drain_until(
    ws,
    *,
    timeout: float,
    want_types: Optional[set] = None,
) -> Dict[str, Any]:
    """接收直到超时或看到目标 type（如 tts stop）。"""
    want_types = want_types or set()
    deadline = time.monotonic() + timeout
    got = {"texts": [], "types": [], "binary": 0, "tts_stop": False, "stt": None}
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            break
        except Exception:
            break
        if isinstance(raw, bytes):
            got["binary"] += 1
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = msg.get("type")
        got["types"].append(t)
        if t == "stt":
            got["stt"] = msg.get("text") or msg.get("session_id")
            if msg.get("text"):
                got["texts"].append(msg.get("text"))
        if t == "tts" and msg.get("text"):
            got["texts"].append(msg.get("text"))
        if t == "tts" and msg.get("state") == "stop":
            got["tts_stop"] = True
            if "tts_stop" in want_types or not want_types:
                break
        if t in want_types and t != "tts":
            break
    return got


async def run_one_chat(ws, device_id: str, prompt: str, timeout: float) -> Tuple[bool, str, float]:
    t0 = time.perf_counter()
    payload = {
        "type": "listen",
        "mode": "manual",
        "state": "detect",
        "text": prompt,
    }
    await ws.send(json.dumps(payload, ensure_ascii=False))
    got = await drain_until(ws, timeout=timeout, want_types={"tts_stop"})
    dt = time.perf_counter() - t0
    # 未绑定设备只会播 OTA/绑定提示，不能算真对话
    texts = " ".join(str(x) for x in got.get("texts") or [])
    stt = str(got.get("stt") or "")
    blob = (texts + " " + stt).lower()
    if any(
        k in blob
        for k in (
            "没有找到该设备",
            "绑定码",
            "请登录控制面板",
            "ota",
            "bind",
        )
    ):
        return False, f"bind_or_unregistered dt={dt:.2f}s stt={stt!r}", dt
    if got["tts_stop"] or "tts" in got["types"] or got["binary"] > 0:
        return True, f"ok dt={dt:.2f}s types={got['types'][:8]}", dt
    return False, f"no_tts dt={dt:.2f}s types={got['types'][:8]}", dt


async def run_one_asr(
    ws, device_id: str, opus_frames: List[bytes], timeout: float
) -> Tuple[bool, str, float]:
    t0 = time.perf_counter()
    await ws.send(
        json.dumps({"type": "listen", "mode": "manual", "state": "start"}, ensure_ascii=False)
    )
    for fr in opus_frames:
        await ws.send(fr)
        await asyncio.sleep(0.02)
    await ws.send(
        json.dumps({"type": "listen", "mode": "manual", "state": "stop"}, ensure_ascii=False)
    )
    got = await drain_until(ws, timeout=timeout, want_types={"tts_stop"})
    dt = time.perf_counter() - t0
    # 空识别也可能降级播报 → 仍算链路跑通
    ok = got["tts_stop"] or got["binary"] > 0 or "stt" in got["types"] or "tts" in got["types"]
    return ok, f"{'ok' if ok else 'fail'} dt={dt:.2f}s stt={got.get('stt')!r} types={got['types'][:8]}", dt


def print_sample(prefix: str, s: Sample) -> None:
    parts = [f"{prefix} clients={s.n_clients}"]
    if s.active_connections is not None:
        parts.append(f"active={s.active_connections}")
    if s.rss_mb is not None:
        parts.append(f"rss={s.rss_mb:.1f}MB")
    if s.num_fds is not None:
        parts.append(f"handles/fds={s.num_fds}")
    if s.cpu_percent is not None:
        parts.append(f"cpu={s.cpu_percent:.1f}%")
    print("  " + " | ".join(parts))


async def bench_idle(args, server_pid: Optional[int]) -> BenchResult:
    print("\n=== IDLE：纯挂机连接爬坡 ===")
    # 预热 cpu_percent
    sample_process(server_pid)
    await asyncio.sleep(0.3)

    sessions: List[Any] = []
    samples: List[Sample] = []
    fail_reason = ""
    max_ok = 0

    try:
        probe0 = http_probe(args.url)
        max_conn = parse_max(probe0)
        print(f"服务端 max_connections={max_conn}（爬坡勿长期超过此值）")
    except Exception as e:
        print(f"WARN: HTTP probe 失败: {e}")

    target = args.idle_target or args.target
    step = args.step
    n = 0
    while n < target:
        batch = min(step, target - n)
        batch_fail = 0
        for i in range(batch):
            ws, err, _ = await open_session(args.url, n + i)
            if ws is None:
                batch_fail += 1
                fail_reason = err or "unknown"
                break
            sessions.append(ws)
        n = len(sessions)
        # 唤醒一次 CPU 采样
        sample_process(server_pid)
        await asyncio.sleep(args.hold_seconds)
        rss, cpu, fds = sample_process(server_pid)
        try:
            active = parse_active(http_probe(args.url))
        except Exception:
            active = None
        s = Sample(time.time(), n, rss, cpu, fds, active)
        samples.append(s)
        print_sample(f"hold {args.hold_seconds}s", s)
        max_ok = n
        if batch_fail:
            print(f"建连失败，停止爬坡: {fail_reason}")
            break
        if rss is not None and args.rss_limit_mb and rss >= args.rss_limit_mb:
            print(f"达到 RSS 上限 {args.rss_limit_mb}MB，停止")
            break
        if fds is not None and args.fd_limit and fds >= args.fd_limit:
            print(f"达到句柄/FD 上限 {args.fd_limit}，停止")
            break

    # 收尾再采一次
    sample_process(server_pid)
    await asyncio.sleep(1.0)
    rss, cpu, fds = sample_process(server_pid)
    try:
        active = parse_active(http_probe(args.url))
    except Exception:
        active = None
    samples.append(Sample(time.time(), len(sessions), rss, cpu, fds, active))
    print_sample("final", samples[-1])

    for ws in sessions:
        try:
            await ws.close()
        except Exception:
            pass
    await asyncio.sleep(1.0)

    detail = (
        f"max_held={max_ok}; last_rss={rss}; last_fds={fds}; "
        f"last_cpu={cpu}; stop={fail_reason or 'target_or_limit'}"
    )
    print(f"IDLE 结论: {detail}")
    return BenchResult(mode="idle", ok=max_ok > 0, detail=detail, samples=samples, extra={"max_held": max_ok})


async def bench_active(args, server_pid: Optional[int], mode: str) -> BenchResult:
    title = "CHAT：同时对话（listen detect → LLM+TTS）" if mode == "chat" else "ASR：同时识别+对话（audio → ASR+LLM+TTS）"
    print(f"\n=== {title} ===")
    concurrency = args.chat_concurrency if mode == "chat" else args.asr_concurrency
    concurrency = concurrency or args.concurrency

    opus_frames: Optional[List[bytes]] = None
    if mode == "asr":
        try:
            if args.audio_wav:
                pcm = load_wav_pcm16_mono_16k(args.audio_wav)
            else:
                print("未指定 --audio-wav，使用合成 440Hz 音调（可能空识别，但仍打满 ASR 路径）")
                pcm = build_tone_pcm(1.2)
            opus_frames = pcm_to_opus_frames(pcm)
            print(f"Opus 帧数={len(opus_frames)}")
        except Exception as e:
            return BenchResult(
                mode="asr",
                ok=False,
                detail=f"无法准备 ASR 音频（缺少 Opus 库或 wav 不合规）: {e}。"
                f"可改跑 --mode chat，或安装系统 Opus 库后重试。",
            )

    sample_process(server_pid)
    await asyncio.sleep(0.2)

    # 先建齐连接
    sessions: List[Any] = []
    errors: List[str] = []
    for i in range(concurrency):
        ws, err, _ = await open_session(args.url, 10_000 + i)
        if ws is None:
            errors.append(err or "open_failed")
            break
        sessions.append(ws)
    print(f"已建连 {len(sessions)}/{concurrency}")
    if not sessions:
        return BenchResult(mode=mode, ok=False, detail=f"无法建连: {errors[:3]}")

    sample_process(server_pid)
    await asyncio.sleep(0.5)
    rss0, cpu0, fds0 = sample_process(server_pid)
    try:
        active0 = parse_active(http_probe(args.url))
    except Exception:
        active0 = None
    s0 = Sample(time.time(), len(sessions), rss0, cpu0, fds0, active0)
    print_sample("before_burst", s0)

    async def one(idx: int, ws) -> Tuple[bool, str, float]:
        if mode == "chat":
            return await run_one_chat(ws, _mac(idx), args.prompt, args.round_timeout)
        assert opus_frames is not None
        return await run_one_asr(ws, _mac(idx), opus_frames, args.round_timeout)

    stop_cpu = asyncio.Event()
    cpu_task = asyncio.create_task(sample_cpu_during(server_pid, stop_cpu, interval=0.25))
    t_burst = time.perf_counter()
    results = await asyncio.gather(
        *[one(i, ws) for i, ws in enumerate(sessions)], return_exceptions=True
    )
    burst_dt = time.perf_counter() - t_burst
    stop_cpu.set()
    cpu_stats = await cpu_task

    ok_n = 0
    lats: List[float] = []
    fail_samples: List[str] = []
    for r in results:
        if isinstance(r, Exception):
            fail_samples.append(f"exc:{type(r).__name__}:{r}")
            continue
        success, detail, lat = r
        if success:
            ok_n += 1
            lats.append(lat)
        else:
            fail_samples.append(detail)

    sample_process(server_pid)
    await asyncio.sleep(0.5)
    rss1, cpu1, fds1 = sample_process(server_pid)
    try:
        active1 = parse_active(http_probe(args.url))
    except Exception:
        active1 = None
    s1 = Sample(time.time(), len(sessions), rss1, cpu1, fds1, active1)
    print_sample("after_burst", s1)

    for ws in sessions:
        try:
            await ws.close()
        except Exception:
            pass

    p50 = sorted(lats)[len(lats) // 2] if lats else None
    cpu_avg = cpu_stats.get("avg")
    cpu_peak = cpu_stats.get("peak")
    cpu_n = cpu_stats.get("n") or 0
    pin = getattr(args, "pin_cores", 0) or 0
    if cpu_avg is not None and cpu_peak is not None:
        print(
            f"  burst_cpu samples={cpu_n} avg={cpu_avg:.1f}% peak={cpu_peak:.1f}% "
            f"(100%≈1逻辑核"
            + (f"; 已绑{pin}核→理论峰值≈{pin * 100}%" if pin else "")
            + ")"
        )
        if pin == 1 and cpu_peak > 0:
            # 粗外推：按 peak 线性，并给出 70% 安全水位建议
            headroom = max(cpu_peak, 1.0)
            est2 = int(len(sessions) * (2 * 100) / headroom)
            est4 = int(len(sessions) * (4 * 100) / headroom)
            safe2 = int(est2 * 0.7)
            safe4 = int(est4 * 0.7)
            print(
                f"  粗外推(按 peak 线性，忽略上游限流/GIL): "
                f"2核≈{est2}路(建议≤{safe2}), 4核≈{est4}路(建议≤{safe4})"
            )

    detail = (
        f"concurrency={len(sessions)} ok={ok_n}/{len(sessions)} "
        f"burst={burst_dt:.2f}s p50={p50}; "
        f"rss {rss0}->{rss1}MB fds {fds0}->{fds1} "
        f"cpu_idle {cpu0}->{cpu1} burst_cpu_avg={cpu_avg} peak={cpu_peak}"
    )
    print(f"{mode.upper()} 结论: {detail}")
    if fail_samples:
        print("失败样例:")
        for line in fail_samples[:5]:
            print(f"  - {line}")
    return BenchResult(
        mode=mode,
        ok=ok_n > 0,
        detail=detail,
        samples=[s0, s1],
        extra={
            "ok": ok_n,
            "total": len(sessions),
            "p50": p50,
            "fails": fail_samples[:10],
            "burst_cpu_avg": cpu_avg,
            "burst_cpu_peak": cpu_peak,
            "burst_cpu_samples": cpu_n,
        },
    )


async def async_main(args) -> int:
    _AUTH.auth = await resolve_smoke_auth(args)
    print(f"Auth: source={_AUTH.auth.source} enabled={_AUTH.auth.enabled}")
    print(f"URL: {args.url}")

    server_pid = args.server_pid
    if args.auto_find_server and not server_pid:
        parsed = urlparse(args.url)
        port = parsed.port or 8000
        server_pid = find_server_pid(port)
        print(f"auto-find-server port={port} => pid={server_pid}")
    if not server_pid:
        print("WARN: 未指定 --server-pid / --auto-find-server，将无法采 RSS/CPU/FD，也无法绑核")

    if args.pin_cores and server_pid:
        try:
            cores = pin_process_cores(server_pid, args.pin_cores)
            print(f"已绑定 PID={server_pid} 到 CPU {cores}（共 {args.pin_cores} 核）")
        except Exception as e:
            print(f"ERROR: 绑核失败: {e}")
            return 2
    elif args.pin_cores and not server_pid:
        print("ERROR: --pin-cores 需要 --server-pid 或 --auto-find-server")
        return 2

    # 初始化 cpu_percent 基线
    sample_process(server_pid)

    results: List[BenchResult] = []
    if args.mode in ("idle", "both"):
        results.append(await bench_idle(args, server_pid))
    if args.mode in ("chat", "both"):
        results.append(await bench_active(args, server_pid, "chat"))
    if args.mode == "asr":
        results.append(await bench_active(args, server_pid, "asr"))
    if args.mode == "both" and args.also_asr:
        results.append(await bench_active(args, server_pid, "asr"))

    print("\n======== SUMMARY ========")
    for r in results:
        print(f"[{'PASS' if r.ok else 'FAIL'}] {r.mode}: {r.detail}")
    return 0 if all(r.ok for r in results) else 1


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="xiaozhi-server 单机容量压测（可绑核）")
    p.add_argument("--url", default="ws://127.0.0.1:8000/xiaozhi/v1/")
    p.add_argument(
        "--mode",
        choices=["idle", "chat", "asr", "both"],
        default="idle",
        help="idle=挂机爬坡; chat=并发对话; asr=并发识别+对话; both=idle+chat",
    )
    p.add_argument("--also-asr", action="store_true", help="both 模式下额外跑 asr")
    p.add_argument("--server-pid", type=int, default=None, help="xiaozhi-server 进程 PID")
    p.add_argument("--auto-find-server", action="store_true", help="按 WS 端口自动找监听进程")
    p.add_argument("--pin-cores", type=int, default=0, help="限制服务端占用前 N 个逻辑核，如 1")

    p.add_argument("--target", type=int, default=2000, help="idle 目标连接数（兼容旧参数）")
    p.add_argument("--idle-target", type=int, default=None, help="idle 目标连接数（优先）")
    p.add_argument("--step", type=int, default=100, help="idle 每批新建连接数")
    p.add_argument("--hold-seconds", type=float, default=8.0, help="idle 每阶梯持有观察秒数")
    p.add_argument("--rss-limit-mb", type=float, default=0, help="RSS 超过则停止 idle 爬坡，0=不限")
    p.add_argument("--fd-limit", type=int, default=0, help="句柄/FD 超过则停止，0=不限")

    p.add_argument("--concurrency", type=int, default=10, help="chat/asr 默认并发")
    p.add_argument("--chat-concurrency", type=int, default=None)
    p.add_argument("--asr-concurrency", type=int, default=None)
    p.add_argument("--prompt", default="用一句话介绍你自己", help="chat 模式 detect 文本")
    p.add_argument("--round-timeout", type=float, default=90.0, help="单路等待 TTS 超时秒")
    p.add_argument("--audio-wav", default=None, help="asr 模式：16kHz mono 16bit wav")

    p.add_argument("--auth-key", default=None)
    p.add_argument("--token", default=None)
    p.add_argument("--no-auth", action="store_true")
    return p


def main() -> None:
    args = build_argparser().parse_args()
    try:
        raise SystemExit(asyncio.run(async_main(args)))
    except KeyboardInterrupt:
        print("\n中断")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
