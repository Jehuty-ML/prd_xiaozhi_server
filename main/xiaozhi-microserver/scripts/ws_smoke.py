"""Phase-3 smoke: hello + listen/detect through agent LLM pipeline + optional abort."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def run(url: str, text: str, timeout: float, abort: bool) -> int:
    try:
        import websockets
    except ImportError:
        print("websockets not installed; pip install websockets", file=sys.stderr)
        return 2

    print(f"Connecting {url} ...")
    async with websockets.connect(
        url,
        additional_headers={"Device-Id": "smoke-001", "Client-Id": "smoke-client"},
    ) as ws:
        hello = await asyncio.wait_for(ws.recv(), timeout=timeout)
        print("<<", hello)
        try:
            hello_obj = json.loads(hello)
        except json.JSONDecodeError:
            print("SMOKE FAIL: hello is not JSON", file=sys.stderr)
            return 1
        if hello_obj.get("type") != "hello" or not hello_obj.get("session_id"):
            print("SMOKE FAIL: missing protocol hello/session_id", file=sys.stderr)
            return 1

        # Client hello (optional features)
        client_hello = {
            "type": "hello",
            "audio_params": hello_obj.get("audio_params")
            or {"format": "opus", "sample_rate": 24000},
        }
        await ws.send(json.dumps(client_hello))
        hello2 = await asyncio.wait_for(ws.recv(), timeout=timeout)
        print("<<", hello2)

        payload = json.dumps(
            {"type": "listen", "state": "detect", "text": text}, ensure_ascii=False
        )
        print(">>", payload)
        await ws.send(payload)

        got_tts = False
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
            except asyncio.TimeoutError:
                break
            if isinstance(msg, bytes):
                print(f"<< bytes[{len(msg)}]: {msg[:80]!r}")
                if msg.startswith(b"XIAOZHI_TTS_STUB:"):
                    got_tts = True
            else:
                print("<<", msg)
                try:
                    data = json.loads(msg)
                    if data.get("type") == "tts":
                        got_tts = True
                except json.JSONDecodeError:
                    pass
            if got_tts:
                break

        if not got_tts:
            print("SMOKE FAIL: no TTS frame within timeout", file=sys.stderr)
            return 1

        if abort:
            await ws.send(json.dumps({"type": "abort", "reason": "smoke"}))
            got_stop = False
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                remaining = deadline - asyncio.get_event_loop().time()
                try:
                    stop = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
                except asyncio.TimeoutError:
                    break
                print("<<", stop if not isinstance(stop, bytes) else f"bytes[{len(stop)}]")
                if isinstance(stop, str):
                    try:
                        stop_obj = json.loads(stop)
                    except json.JSONDecodeError:
                        continue
                    if stop_obj.get("type") == "tts" and stop_obj.get("state") == "stop":
                        got_stop = True
                        break
            if not got_stop:
                print("SMOKE FAIL: abort did not yield tts stop", file=sys.stderr)
                return 1

        print("SMOKE OK: protocol hello + TTS downlink")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8103/xiaozhi/v1/?device-id=smoke-001",
    )
    parser.add_argument("--text", default="hello-xiaozhi")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--abort", action="store_true", help="also exercise abort")
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(run(args.url, args.text, args.timeout, args.abort))
    )


if __name__ == "__main__":
    main()
