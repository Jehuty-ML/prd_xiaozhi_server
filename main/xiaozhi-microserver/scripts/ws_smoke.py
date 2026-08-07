"""Phase-1 smoke test: connect to xiaozhi-access WS and inject text through the stub pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def run(url: str, text: str, timeout: float) -> int:
    try:
        import websockets
    except ImportError:
        print("websockets not installed; pip install websockets", file=sys.stderr)
        return 2

    print(f"Connecting {url} ...")
    async with websockets.connect(url) as ws:
        hello = await asyncio.wait_for(ws.recv(), timeout=timeout)
        print("<<", hello)
        payload = json.dumps({"type": "listen", "text": text}, ensure_ascii=False)
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
                preview = msg[:80]
                print(f"<< bytes[{len(msg)}]: {preview!r}")
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

        if got_tts:
            print("SMOKE OK: received TTS downlink")
            return 0
        print("SMOKE FAIL: no TTS frame within timeout", file=sys.stderr)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8103/ws?device-id=smoke-001",
    )
    parser.add_argument("--text", default="hello-xiaozhi")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.url, args.text, args.timeout)))


if __name__ == "__main__":
    main()
