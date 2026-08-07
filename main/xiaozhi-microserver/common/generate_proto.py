"""Generate Python gRPC stubs from common/proto into common/generated/xiaozhi."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROTO_DIR = ROOT / "proto"
OUT_DIR = ROOT / "generated" / "xiaozhi"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "__init__.py").write_text("", encoding="utf-8")
    protos = sorted(PROTO_DIR.glob("*.proto"))
    if not protos:
        print("No .proto files found", file=sys.stderr)
        return 1

    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{PROTO_DIR}",
        f"--python_out={OUT_DIR}",
        f"--grpc_python_out={OUT_DIR}",
        *[str(p) for p in protos],
    ]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        return result.returncode

    # Fix relative imports for package layout: `import audio_pb2` -> `from . import audio_pb2`
    for grpc_file in OUT_DIR.glob("*_pb2_grpc.py"):
        text = grpc_file.read_text(encoding="utf-8")
        for name in ("audio", "command", "session", "admin"):
            text = text.replace(
                f"import {name}_pb2 as {name}__pb2",
                f"from . import {name}_pb2 as {name}__pb2",
            )
        grpc_file.write_text(text, encoding="utf-8")

    print(f"Generated stubs in {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
