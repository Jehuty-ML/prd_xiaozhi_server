from __future__ import annotations

import json

from loguru import logger
from xiaozhi import admin_pb2, admin_pb2_grpc
from app.core.handler.config_store import ConfigStore


class ModelAdminServicer(admin_pb2_grpc.ModelAdminServiceServicer):
    def __init__(self, store: ConfigStore) -> None:
        self.store = store

    def GetConfig(self, request, context):  # noqa: N802, ANN001
        key = request.key or ""
        logger.info(f"GetConfig service={request.service_name} key={key or '*'}")
        return admin_pb2.GetConfigResponse(
            code=0, msg="ok", config_json=self.store.as_json(key)
        )

    def ReloadConfig(self, request, context):  # noqa: N802, ANN001
        reason = request.reason or "rpc"
        self.store.reload(reason=reason)
        return admin_pb2.ReloadConfigResponse(code=0, msg="ok", result="reloaded")

    def Health(self, request, context):  # noqa: N802, ANN001
        detail = {
            "status": "ok",
            "service": "xiaozhi-model-admin",
            "phase": 1,
            "config_keys": list(self.store.get().keys()),
        }
        return admin_pb2.HealthResponse(
            code=0,
            msg="ok",
            status="ok",
            detail_json=json.dumps(detail, ensure_ascii=False),
        )
