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
from xiaozhi_common.constants import (  # noqa: E402
    CONTROL_ADMIN_HTTP_PORT,
    CONTROL_ADMIN_SERVICE,
)
from xiaozhi_common.logging import setup_logging  # noqa: E402


def build_config():
    parser = argparse.ArgumentParser(description="xiaozhi-control-admin")
    add_common_arguments(
        parser,
        service_name=CONTROL_ADMIN_SERVICE,
        server_name="xiaozhi_control_admin",
        default_grpc_port=50056,
    )
    parser.set_defaults(http_port=CONTROL_ADMIN_HTTP_PORT)
    args, _ = parser.parse_known_args()
    cfg = config_from_args(args)
    if cfg.http_port is None:
        cfg.http_port = CONTROL_ADMIN_HTTP_PORT
    return cfg


def main() -> None:
    config = build_config()
    setup_logging(config.log_level, config.log_file)
    from app.server.rpc_server import serve

    serve(config)


if __name__ == "__main__":
    main()
