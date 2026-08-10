"""Peer gates against access GetDeviceState (micro_service style)."""

from __future__ import annotations

from typing import Optional

from loguru import logger
from xiaozhi import command_pb2, command_pb2_grpc
from xiaozhi_common.constants import ACCESS_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool
from xiaozhi_common.session import (
    SessionState,
    can_continue_play,
    can_continue_think,
    can_send_asr,
    can_start_listen,
    can_start_play,
    can_start_think,
    parse_session_state,
)


def fetch_device_state(
    pool: GrpcClientPool, client_id: str, *, message_id: str = ""
) -> Optional[SessionState]:
    if not client_id:
        return None
    try:
        stub = command_pb2_grpc.AccessCommandServiceStub(pool.channel(ACCESS_SERVICE))
        resp = stub.GetDeviceState(
            command_pb2.DeviceStateRequest(
                message_id=message_id or client_id,
                client_id=client_id,
            ),
            metadata=pool.metadata(client_id, message_id or client_id),
            timeout=3,
        )
        if int(resp.code) != 0:
            logger.warning(f"GetDeviceState failed client={client_id} msg={resp.msg}")
            return None
        return parse_session_state(resp.state)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"GetDeviceState error client={client_id}: {exc}")
        return None


def send_state_command(
    pool: GrpcClientPool,
    client_id: str,
    command: str,
    *,
    message_id: str = "",
) -> bool:
    """Ask access to apply an FSM event; False if illegal / RPC fail."""
    try:
        stub = command_pb2_grpc.AccessCommandServiceStub(pool.channel(ACCESS_SERVICE))
        resp = stub.SendCommand(
            command_pb2.CommandRequest(
                message_id=message_id or client_id,
                client_id=client_id,
                command=command,
            ),
            metadata=pool.metadata(client_id, message_id or client_id),
            timeout=3,
        )
        if int(resp.code) == 2:
            logger.info(
                f"FSM reject client={client_id} command={command} state={resp.result}"
            )
            return False
        return int(resp.code) == 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"SendCommand error client={client_id} cmd={command}: {exc}")
        return False


def gate_start_listen(pool: GrpcClientPool, client_id: str, message_id: str = "") -> bool:
    state = fetch_device_state(pool, client_id, message_id=message_id)
    ok = can_start_listen(state)
    if not ok:
        logger.info(f"gate listen deny client={client_id} state={state}")
    return ok


def gate_send_asr(pool: GrpcClientPool, client_id: str, message_id: str = "") -> bool:
    state = fetch_device_state(pool, client_id, message_id=message_id)
    ok = can_send_asr(state)
    if not ok:
        logger.info(f"gate asr deny client={client_id} state={state}")
    return ok


def gate_start_think(pool: GrpcClientPool, client_id: str, message_id: str = "") -> bool:
    state = fetch_device_state(pool, client_id, message_id=message_id)
    ok = can_start_think(state)
    if not ok:
        logger.info(f"gate think deny client={client_id} state={state}")
    return ok


def gate_continue_think(
    pool: GrpcClientPool, client_id: str, message_id: str = ""
) -> bool:
    state = fetch_device_state(pool, client_id, message_id=message_id)
    return can_continue_think(state)


def gate_start_play(pool: GrpcClientPool, client_id: str, message_id: str = "") -> bool:
    state = fetch_device_state(pool, client_id, message_id=message_id)
    ok = can_start_play(state)
    if not ok:
        logger.info(f"gate play deny client={client_id} state={state}")
    return ok


def gate_continue_play(
    pool: GrpcClientPool, client_id: str, message_id: str = ""
) -> bool:
    state = fetch_device_state(pool, client_id, message_id=message_id)
    return can_continue_play(state)
