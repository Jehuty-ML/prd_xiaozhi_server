from __future__ import annotations

import json

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc
from xiaozhi_common.grpc.client import GrpcClientPool

from app.core.chat_engine import ChatEngine
from app.core.config_loader import runtime_config
from app.core.device_events import handle_device_event
from app.core.session import session_store
from app.providers.intent import resolve_intent_type
from app.providers.llm import create_llm
from app.providers.memory import create_memory
from app.tools.handler import ToolHandler


class AgentServicer(audio_pb2_grpc.AgentServiceServicer):
    """Phase-3 agent: LLM / Intent / Memory / tools + device MCP/IoT events."""

    def __init__(self, pool: GrpcClientPool) -> None:
        self.pool = pool
        self._rebuild()

    def _rebuild(self) -> None:
        runtime_config.reload()
        cfg = runtime_config.data
        self.cfg = cfg
        self.llm = create_llm(cfg)
        self.memory = create_memory(cfg)
        self.tools = ToolHandler(cfg)
        self.tools.ensure_loaded()
        self.engine = ChatEngine(self.llm, self.memory, self.tools, cfg)
        self.system_prompt = runtime_config.build_system_prompt()
        self.intent_type = resolve_intent_type(cfg)
        logger.info(
            f"Agent runtime ready LLM={type(self.llm).__name__} "
            f"intent={self.intent_type}"
        )

    def SendText(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        text = (request.text or "").strip() or "你好"
        session = session_store.get_or_create(
            client_id,
            self.pool,
            self.cfg,
            prompt=self.system_prompt,
            intent_type=self.intent_type,
        )
        logger.info(f"Agent SendText client_id={client_id} text={text!r}")
        try:
            reply = self.engine.chat(session, text)
            return audio_pb2.TextResponse(code=0, msg="ok", result=reply or "")
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Agent SendText failed: {exc}")
            return audio_pb2.TextResponse(code=1, msg=str(exc), result="")

    def HandleDeviceEvent(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        event_type = request.event_type or ""
        try:
            payload = json.loads(request.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        session = session_store.get_or_create(
            client_id,
            self.pool,
            self.cfg,
            prompt=self.system_prompt,
            intent_type=self.intent_type,
        )
        result = handle_device_event(session, event_type, payload)
        logger.info(
            f"DeviceEvent client={client_id} type={event_type} result={result}"
        )
        return audio_pb2.DeviceEventResponse(code=0, msg="ok", result=result)

    def Abort(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        reason = request.reason or "abort"
        ok = session_store.abort(client_id, reason)
        logger.info(f"Agent Abort client={client_id} reason={reason} ok={ok}")
        return audio_pb2.AgentAbortResponse(
            code=0, msg="ok", result="aborted" if ok else "no_session"
        )
