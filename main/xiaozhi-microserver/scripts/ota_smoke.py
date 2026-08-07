"""Phase-2 smoke: OTA POST on model-admin returns websocket URL."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8004")
    args = parser.parse_args()

    url = args.base.rstrip("/") + "/xiaozhi/ota/"
    req = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "device-id": "smoke-ota-001",
            "client-id": "smoke-ota-client",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            print("<<", body)
            data = json.loads(body)
    except urllib.error.HTTPError as exc:
        print(f"SMOKE FAIL: HTTP {exc.code}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"SMOKE FAIL: {exc}", file=sys.stderr)
        return 1

    ws = (data.get("websocket") or {}).get("url")
    if not ws:
        print("SMOKE FAIL: missing websocket.url", file=sys.stderr)
        return 1
    print(f"SMOKE OK: OTA websocket.url={ws}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
