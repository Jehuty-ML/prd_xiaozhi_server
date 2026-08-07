from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent
MICRO_ROOT = SERVICE_ROOT.parent
for p in (MICRO_ROOT / "common", MICRO_ROOT / "common" / "generated", SERVICE_ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

import argparse  # noqa: E402

from xiaozhi_common.config import add_common_arguments, config_from_args  # noqa: E402
from xiaozhi_common.constants import AGENT_SERVICE  # noqa: E402
from xiaozhi_common.logging import setup_logging  # noqa: E402


def build_config():
    parser = argparse.ArgumentParser(description="xiaozhi-agent")
    add_common_arguments(
        parser,
        service_name=AGENT_SERVICE,
        server_name="xiaozhi_agent",
        default_grpc_port=50052,
    )
    args, _ = parser.parse_known_args()
    return config_from_args(args)


def main() -> None:
    config = build_config()
    setup_logging(config.log_level, config.log_file)
    from app.server.rpc_server import serve

    serve(config)


if __name__ == "__main__":
    main()
