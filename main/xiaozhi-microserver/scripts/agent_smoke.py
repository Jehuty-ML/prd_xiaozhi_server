"""Phase-3 smoke: EchoLLM chat, get_time tool, exit intent, MCP/IoT proxy."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def _recv_until_tts(ws, timeout: float) -> tuple[bool, list[str]]:
    got_tts = False
    tts_texts: list[str] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        remaining = deadline - loop.time()
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            break
        if isinstance(msg, bytes):
            print(f"<< bytes[{len(msg)}]")
            if msg.startswith(b"XIAOZHI_TTS_STUB:"):
                got_tts = True
                tts_texts.append(msg.decode("utf-8", errors="ignore"))
        else:
            print("<<", msg)
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "tts":
                got_tts = True
                if data.get("text"):
                    tts_texts.append(str(data["text"]))
        if got_tts and tts_texts:
            # Drain a brief window for multi-part TTS
            if loop.time() + 0.3 >= deadline:
                break
    return got_tts, tts_texts


async def run(
    url: str,
    text: str,
    timeout: float,
    mcp: bool,
    iot: bool,
    exit_intent: bool,
) -> int:
    try:
        import websockets
    except ImportError:
        print("websockets not installed; pip install websockets", file=sys.stderr)
        return 2

    print(f"Connecting {url} ...")
    async with websockets.connect(
        url,
        additional_headers={"Device-Id": "smoke-agent", "Client-Id": "smoke-agent"},
    ) as ws:
        hello = await asyncio.wait_for(ws.recv(), timeout=timeout)
        print("<<", hello)
        hello_obj = json.loads(hello)
        if hello_obj.get("type") != "hello":
            print("SMOKE FAIL: expected hello", file=sys.stderr)
            return 1

        payload = json.dumps(
            {"type": "listen", "state": "detect", "text": text}, ensure_ascii=False
        )
        print(">>", payload)
        await ws.send(payload)

        got_tts, tts_texts = await _recv_until_tts(ws, timeout)
        if not got_tts:
            print("SMOKE FAIL: no TTS from agent pipeline", file=sys.stderr)
            return 1
        print("TTS texts:", tts_texts)
        joined = "\n".join(tts_texts)
        # EchoLLM get_time path should mention year / clock
        if "几点" in text or "时间" in text:
            if "年" not in joined and ":" not in joined:
                print("SMOKE FAIL: get_time reply missing time content", file=sys.stderr)
                return 1

        if mcp:
            mcp_msg = {
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "tools": [
                            {
                                "name": "self.light.turn_on",
                                "description": "Turn on light",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                        ]
                    },
                },
            }
            await ws.send(json.dumps(mcp_msg, ensure_ascii=False))
            ack = await asyncio.wait_for(ws.recv(), timeout=timeout)
            print("<< mcp", ack)
            ack_obj = json.loads(ack) if isinstance(ack, str) else {}
            if ack_obj.get("status") != "ok":
                print("SMOKE FAIL: mcp proxy status", file=sys.stderr)
                return 1
            if "mcp_tools=" not in str(ack_obj.get("message") or ""):
                print(
                    f"SMOKE FAIL: expected mcp_tools= ack, got {ack_obj.get('message')!r}",
                    file=sys.stderr,
                )
                return 1

        if iot:
            iot_msg = {
                "type": "iot",
                "descriptors": [
                    {
                        "name": "Lamp",
                        "description": "desk lamp",
                        "properties": {
                            "power": {"type": "boolean", "description": "on"}
                        },
                        "methods": {},
                    }
                ],
            }
            await ws.send(json.dumps(iot_msg, ensure_ascii=False))
            ack = await asyncio.wait_for(ws.recv(), timeout=timeout)
            print("<< iot", ack)
            ack_obj = json.loads(ack) if isinstance(ack, str) else {}
            if ack_obj.get("status") != "ok":
                print("SMOKE FAIL: iot proxy", file=sys.stderr)
                return 1
            if "iot_descriptors=" not in str(ack_obj.get("message") or ""):
                print(
                    f"SMOKE FAIL: expected iot_descriptors= ack, got {ack_obj.get('message')!r}",
                    file=sys.stderr,
                )
                return 1

        if exit_intent:
            exit_payload = json.dumps(
                {"type": "listen", "state": "detect", "text": "再见"},
                ensure_ascii=False,
            )
            print(">>", exit_payload)
            await ws.send(exit_payload)
            got_exit, exit_texts = await _recv_until_tts(ws, timeout)
            if not got_exit:
                print("SMOKE FAIL: no TTS for exit intent", file=sys.stderr)
                return 1
            print("exit TTS:", exit_texts)
            if not any("再见" in t or "愉快" in t for t in exit_texts):
                print("SMOKE FAIL: exit farewell missing", file=sys.stderr)
                return 1

        print("SMOKE OK: phase-3 agent")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="ws://127.0.0.1:8103/xiaozhi/v1/?device-id=smoke-agent",
    )
    parser.add_argument("--text", default="现在几点了")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--iot", action="store_true")
    parser.add_argument(
        "--exit",
        dest="exit_intent",
        action="store_true",
        help="also exercise handle_exit_intent",
    )
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            run(
                args.url,
                args.text,
                args.timeout,
                args.mcp,
                args.iot,
                args.exit_intent,
            )
        )
    )


if __name__ == "__main__":
    main()
