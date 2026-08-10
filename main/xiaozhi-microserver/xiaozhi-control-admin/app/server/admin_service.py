from __future__ import annotations

import json

from loguru import logger
from xiaozhi import admin_pb2, admin_pb2_grpc
from app.api.metrics import observe_reload
from app.core.handler.config_store import ConfigStore


class ModelAdminServicer(admin_pb2_grpc.ModelAdminServiceServicer):
    def __init__(self, store: ConfigStore) -> None:
        self.store = store

    def GetConfig(self, request, context):  # noqa: N802, ANN001
        key = request.key or ""
        service = request.service_name or ""
        logger.info(f"GetConfig service={service or '-'} key={key or '*'}")
        return admin_pb2.GetConfigResponse(
            code=0,
            msg="ok",
            config_json=self.store.as_json(key, for_service=service),
        )

    def ReloadConfig(self, request, context):  # noqa: N802, ANN001
        reason = request.reason or "rpc"
        try:
            self.store.reload(reason=reason, broadcast=True)
            observe_reload(True)
            return admin_pb2.ReloadConfigResponse(
                code=0, msg="ok", result="reloaded"
            )
        except Exception as exc:  # noqa: BLE001
            observe_reload(False)
            logger.exception(f"ReloadConfig failed: {exc}")
            return admin_pb2.ReloadConfigResponse(
                code=1, msg=str(exc), result=""
            )

    def Health(self, request, context):  # noqa: N802, ANN001
        detail = {
            "status": "ok",
            "service": "xiaozhi-control-admin",
            "phase": 2,
            "config_keys": list(self.store.get().keys()),
            "read_config_from_api": bool(self.store.get("read_config_from_api")),
        }
        return admin_pb2.HealthResponse(
            code=0,
            msg="ok",
            status="ok",
            detail_json=json.dumps(detail, ensure_ascii=False),
        )
