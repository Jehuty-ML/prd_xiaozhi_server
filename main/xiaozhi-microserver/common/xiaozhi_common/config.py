from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class BaseServerConfig:
    env: str = "dev"
    group_name: str = "DEV_GROUP"
    env_id: str = "xiaozhi-dev"
    user_name: str = "nacos"
    password: str = "nacos"
    nacos_host: str = "127.0.0.1"
    nacos_port: str = "8848"
    service_name: str = ""
    server_name: str = ""
    grpc_port: Optional[int] = None
    http_port: Optional[int] = None
    heart_interval: int = 5
    rpc_max_connect: int = 32
    log_level: str = "DEBUG"
    log_file: str = "logs/server.log"
    disable_nacos: bool = False
    peers: dict[str, str] = field(default_factory=dict)

    def get_local_ip(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()

    def is_port_in_use(self, port: int, host: str = "127.0.0.1") -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            return sock.connect_ex((host, port)) == 0

    def resolve_grpc_port(self, default_port: int) -> int:
        if self.grpc_port:
            return self.grpc_port
        port = default_port
        while self.is_port_in_use(port):
            port += 1
        return port

    @property
    def nacos_addr(self) -> str:
        return f"{self.nacos_host}:{self.nacos_port}"

    def is_dev(self) -> bool:
        return self.env == "dev"


def parse_peer_args(peer_args: list[str] | None) -> dict[str, str]:
    """Parse --peer service=host:port repeats into a map."""
    peers: dict[str, str] = {}
    for item in peer_args or []:
        if "=" not in item:
            continue
        name, addr = item.split("=", 1)
        peers[name.strip()] = addr.strip()
    return peers


def add_common_arguments(parser: Any, *, service_name: str, server_name: str, default_grpc_port: int) -> None:
    parser.add_argument("--env", type=str, default="dev")
    parser.add_argument("--group_name", type=str, default="DEV_GROUP")
    parser.add_argument("--env_id", type=str, default="xiaozhi-dev")
    parser.add_argument("--user_name", type=str, default="nacos")
    parser.add_argument("--password", type=str, default="nacos")
    parser.add_argument("--nacos_host", type=str, default="127.0.0.1")
    parser.add_argument("--nacos_port", type=str, default="8848")
    parser.add_argument("--service_name", type=str, default=service_name)
    parser.add_argument("--server_name", type=str, default=server_name)
    parser.add_argument("--grpc_port", type=int, default=default_grpc_port)
    parser.add_argument("--http_port", type=int, default=None)
    parser.add_argument("--heart_interval", type=int, default=5)
    parser.add_argument("--rpc_max_connect", type=int, default=32)
    parser.add_argument("--log_level", type=str, default="DEBUG", help="Console log level")
    parser.add_argument("--log_file", type=str, default="logs/server.log")
    parser.add_argument("--disable_nacos", action="store_true", help="Skip Nacos register/discover")
    parser.add_argument(
        "--peer",
        action="append",
        default=[],
        help="Static peer override, e.g. --peer xiaozhi-agent-grpc-service=127.0.0.1:50052",
    )


def config_from_args(args: Any) -> BaseServerConfig:
    return BaseServerConfig(
        env=args.env,
        group_name=args.group_name,
        env_id=args.env_id,
        user_name=args.user_name,
        password=args.password,
        nacos_host=args.nacos_host,
        nacos_port=args.nacos_port,
        service_name=args.service_name,
        server_name=args.server_name,
        grpc_port=args.grpc_port,
        http_port=getattr(args, "http_port", None),
        heart_interval=args.heart_interval,
        rpc_max_connect=args.rpc_max_connect,
        log_level=args.log_level,
        log_file=args.log_file,
        disable_nacos=bool(getattr(args, "disable_nacos", False)),
        peers=parse_peer_args(getattr(args, "peer", None)),
    )
