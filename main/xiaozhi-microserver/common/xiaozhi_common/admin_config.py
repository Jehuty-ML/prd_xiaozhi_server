"""Pull / apply config snapshots from xiaozhi-control-admin."""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any, Callable, Optional

from loguru import logger

from xiaozhi_common.constants import CONTROL_ADMIN_SERVICE
from xiaozhi_common.grpc.client import GrpcClientPool


def deep_merge(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    for key, value in (overlay or {}).items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def pull_admin_config(
    pool: GrpcClientPool,
    *,
    service_name: str,
    timeout: float = 5.0,
) -> Optional[dict[str, Any]]:
    """Best-effort GetConfig from control-admin."""
    try:
        from xiaozhi import admin_pb2, admin_pb2_grpc
    except ImportError:  # pragma: no cover
        return None
    message_id = uuid.uuid4().hex
    try:
        stub = admin_pb2_grpc.ModelAdminServiceStub(
            pool.channel(CONTROL_ADMIN_SERVICE)
        )
        resp = stub.GetConfig(
            admin_pb2.GetConfigRequest(
                message_id=message_id,
                service_name=service_name,
            ),
            metadata=pool.metadata(message_id=message_id),
            timeout=timeout,
        )
        if int(resp.code) != 0:
            logger.warning(f"GetConfig from admin failed: {resp.msg}")
            return None
        data = json.loads(resp.config_json or "{}")
        if isinstance(data, dict) and data:
            logger.info(f"Config pulled from control-admin for {service_name}")
            return data
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Startup config pull skipped ({service_name}): {exc}")
    return None


def make_apply_config_servicer(
    *,
    apply_fn: Callable[[dict[str, Any], str], None],
):
    """Build a ConfigApplyServiceServicer that calls ``apply_fn(config, reason)``."""
    from xiaozhi import admin_pb2, admin_pb2_grpc

    class _Servicer(admin_pb2_grpc.ConfigApplyServiceServicer):
        def ApplyConfig(self, request, context):  # noqa: N802, ANN001
            reason = request.reason or "rpc"
            try:
                config = json.loads(request.config_json or "{}")
                if not isinstance(config, dict):
                    return admin_pb2.ApplyConfigResponse(
                        code=1, msg="invalid config_json", result=""
                    )
                apply_fn(copy.deepcopy(config), reason)
                return admin_pb2.ApplyConfigResponse(
                    code=0, msg="ok", result="applied"
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"ApplyConfig failed: {exc}")
                return admin_pb2.ApplyConfigResponse(
                    code=1, msg=str(exc), result=""
                )

    return _Servicer()
