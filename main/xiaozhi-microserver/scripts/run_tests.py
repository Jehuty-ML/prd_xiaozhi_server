"""Run unit tests + live smokes (services must already be up for smoke)."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, label: str) -> int:
    print(f"\n=== {label} ===")
    print("+", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT))
    print(f"--- {label} exit={proc.returncode} ---")
    return int(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="xiaozhi-microserver phase-6 tests")
    parser.add_argument("--unit-only", action="store_true")
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument("--skip-ota", action="store_true")
    args = parser.parse_args()

    codes: list[int] = []

    if not args.smoke_only:
        codes.append(
            _run(
                [sys.executable, "-m", "pytest", "tests", "-q", "--tb=short"],
                label="unit",
            )
        )

    if not args.unit_only:
        codes.append(
            _run(
                [sys.executable, "scripts/ws_smoke.py", "--abort"],
                label="ws_smoke+abort",
            )
        )
        codes.append(
            _run(
                [
                    sys.executable,
                    "scripts/agent_smoke.py",
                    "--mcp",
                    "--iot",
                    "--exit",
                ],
                label="agent_smoke",
            )
        )
        if not args.skip_ota:
            codes.append(
                _run([sys.executable, "scripts/ota_smoke.py"], label="ota_smoke")
            )

    failed = [c for c in codes if c != 0]
    if failed:
        print(f"\nFAILED: {len(failed)}/{len(codes)} suites")
        return 1
    print(f"\nALL OK: {len(codes)} suites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
