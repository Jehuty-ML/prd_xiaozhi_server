from __future__ import annotations

import json

from loguru import logger
from xiaozhi import audio_pb2, audio_pb2_grpc
from xiaozhi_common.admin_config import deep_merge
from xiaozhi_common.grpc.client import GrpcClientPool

from app.core.chat_engine import ChatEngine
from app.core.config_loader import runtime_config
from app.core.device_events import handle_device_event
from app.core.session import session_store
from app.providers.intent import resolve_intent_type
from app.providers.llm import create_llm
from app.providers.memory import create_memory
from app.tools.handler import ToolHandler

try:
    from xiaozhi_common.provider_aliases import mirror_provider_aliases
except ImportError:  # pragma: no cover

    def mirror_provider_aliases(config: dict) -> dict:  # type: ignore
        return config


class AgentServicer(audio_pb2_grpc.AgentServiceServicer):
    """Phase-3 agent: LLM / Intent / Memory / tools + device MCP/IoT events."""

    def __init__(self, pool: GrpcClientPool) -> None:
        self.pool = pool
        self._rebuild()

    def _rebuild(self) -> None:
        # Do not reload local yaml here — that would wipe apply_remote merges.
        cfg = runtime_config.data
        self.cfg = cfg
        self.llm = create_llm(cfg)
        self.memory = create_memory(cfg)
        self.tools = ToolHandler(cfg)
        self.tools.ensure_loaded()
        self.engine = ChatEngine(self.llm, self.memory, self.tools, cfg)
        self.system_prompt = runtime_config.build_system_prompt(cfg)
        self.intent_type = resolve_intent_type(cfg)
        logger.info(
            f"Agent runtime ready default_LLM={type(self.llm).__name__} "
            f"intent={self.intent_type} "
            f"(device LLM overrides via ApplySessionConfig)"
        )

    def _session_for(self, client_id: str, device_id: str = ""):
        existing = session_store.get(client_id)
        if existing is not None and existing.config:
            return session_store.get_or_create(
                client_id,
                self.pool,
                existing.config,
                prompt=existing.prompt or self.system_prompt,
                intent_type=existing.intent_type or self.intent_type,
                device_id=device_id or existing.device_id,
                llm=existing.llm,
            )
        return session_store.get_or_create(
            client_id,
            self.pool,
            self.cfg,
            prompt=self.system_prompt,
            intent_type=self.intent_type,
            device_id=device_id,
        )

    def ApplySessionConfig(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        device_id = request.device_id or client_id
        reason = request.reason or "apply_session"
        try:
            private = json.loads(request.config_json or "{}")
        except json.JSONDecodeError:
            private = {}
        if not isinstance(private, dict):
            private = {}
        if not client_id:
            return audio_pb2.ApplySessionConfigResponse(
                code=1, msg="missing client_id", result=""
            )

        merged = deep_merge(dict(self.cfg or {}), private)
        merged = mirror_provider_aliases(merged)
        llm = create_llm(merged)
        intent = resolve_intent_type(merged)
        prompt = runtime_config.build_system_prompt(merged)
        session_store.apply_private_config(
            client_id,
            self.pool,
            merged,
            device_id=device_id,
            prompt=prompt,
            intent_type=intent,
            llm=llm,
        )
        selected = (merged.get("selected_module") or {}).get("LLM")
        logger.info(
            f"ApplySessionConfig client={client_id} device={device_id} "
            f"reason={reason} LLM={selected} impl={type(llm).__name__}"
        )
        return audio_pb2.ApplySessionConfigResponse(
            code=0, msg="ok", result=str(selected or type(llm).__name__)
        )

    def SendText(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        text = (request.text or "").strip() or "你好"
        session = self._session_for(client_id)
        logger.info(f"Agent SendText client_id={client_id} text={text!r}")
        # Same device may arrive twice (detect + fallback); serialize per client.
        lock = session_store.chat_lock(client_id)
        if not lock.acquire(blocking=False):
            logger.warning(
                f"Agent SendText busy client_id={client_id}, aborting previous turn"
            )
            session.mark_abort("superseded")
            lock.acquire()
        try:
            session.reset_abort()
            from xiaozhi import audio_pb2_grpc
            from xiaozhi_common.constants import SPEAKER_SERVICE
            from xiaozhi_common.session import (
                SessionState,
                fetch_device_state,
                is_play_only_mode,
                send_state_command,
            )

            # Barge-in: abort in-flight TTS/LLM before a new chat turn.
            # Include DETECT: wake-word path may have left SPEAKING→DETECT without
            # stopping the speaker; skipping abort causes overlapping audio + FSM clobber.
            state = fetch_device_state(
                self.pool, client_id, message_id=request.message_id or ""
            )
            if state in (
                SessionState.SPEAKING,
                SessionState.THINKING,
                SessionState.DETECT,
            ):
                logger.info(
                    f"Agent SendText barge-in client={client_id} from={state}"
                )
                send_state_command(
                    self.pool,
                    client_id,
                    "abort",
                    message_id=request.message_id or "",
                )
                try:
                    stub = audio_pb2_grpc.AudioSpeakerServiceStub(
                        self.pool.channel(SPEAKER_SERVICE)
                    )
                    stub.Abort(
                        audio_pb2.SpeakerAbortRequest(
                            message_id=request.message_id or "",
                            client_id=client_id,
                            reason="barge_in",
                        ),
                        metadata=self.pool.metadata(
                            client_id, request.message_id or ""
                        ),
                        timeout=5,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"barge-in speaker abort skipped: {exc}")
                session.reset_abort()

            if is_play_only_mode(session.config) or is_play_only_mode(
                session.session_sm
            ):
                deny = session.play_only_deny_text()
                logger.info(f"Agent SendText play_only deny client={client_id}")
                return audio_pb2.TextResponse(code=0, msg="play_only", result=deny)

            if not session.begin_chat():
                # Access may still reject (play_only remote); try listen_start then chat
                send_state_command(
                    self.pool,
                    client_id,
                    "listen_start",
                    message_id=request.message_id or "",
                )
                if not session.begin_chat():
                    deny = session.play_only_deny_text()
                    logger.info(
                        f"Agent SendText chat_start reject client={client_id}"
                    )
                    return audio_pb2.TextResponse(
                        code=0, msg="play_only", result=deny
                    )
            reply = self.engine.chat(session, text)
            logger.info(
                f"Agent SendText done client_id={client_id} "
                f"reply={(reply or '')[:80]!r}"
            )
            return audio_pb2.TextResponse(code=0, msg="ok", result=reply or "")
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"Agent SendText failed: {exc}")
            return audio_pb2.TextResponse(code=1, msg=str(exc), result="")
        finally:
            lock.release()

    def HandleDeviceEvent(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        event_type = request.event_type or ""
        try:
            payload = json.loads(request.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        session = self._session_for(client_id)
        result = handle_device_event(session, event_type, payload)
        logger.info(
            f"DeviceEvent client={client_id} type={event_type} result={result}"
        )
        return audio_pb2.DeviceEventResponse(code=0, msg="ok", result=result)

    def Abort(self, request, context):  # noqa: N802, ANN001
        client_id = request.client_id or getattr(context, "client_id", "")
        reason = request.reason or "abort"
        ok = session_store.abort(client_id, reason)
        # Always stop in-flight TTS; mark_abort alone leaves SpeakSession draining.
        try:
            from xiaozhi_common.constants import SPEAKER_SERVICE

            stub = audio_pb2_grpc.AudioSpeakerServiceStub(
                self.pool.channel(SPEAKER_SERVICE)
            )
            stub.Abort(
                audio_pb2.SpeakerAbortRequest(
                    message_id=request.message_id or "",
                    client_id=client_id,
                    reason=reason,
                ),
                metadata=self.pool.metadata(client_id, request.message_id or ""),
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Agent Abort speaker fan-out skipped: {exc}")
        if reason in ("disconnect", "unbind", "shutdown"):
            session_store.remove(client_id)
        logger.info(f"Agent Abort client={client_id} reason={reason} ok={ok}")
        return audio_pb2.AgentAbortResponse(
            code=0, msg="ok", result="aborted" if ok else "no_session"
        )
