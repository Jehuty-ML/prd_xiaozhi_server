"""Fetch per-device agent-models and push session config to agent / ASR / TTS peers."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
from loguru import logger

from xiaozhi import admin_pb2, admin_pb2_grpc, audio_pb2, audio_pb2_grpc
from xiaozhi_common.constants import (
    AGENT_SERVICE,
    PREPROCESS_SERVICE,
    RECEIVER_SERVICE,
    SPEAKER_SERVICE,
)
from xiaozhi_common.grpc.client import GrpcClientPool

from app.ws.gateway_runtime import gateway_runtime
from app.ws.manager import connection_manager


class DeviceBindNeeded(Exception):
    def __init__(self, bind_code: str):
        self.bind_code = str(bind_code or "").strip()
        super().__init__(f"device needs bind code={self.bind_code}")


class DeviceNotFound(Exception):
    pass


def _manager_api(config: dict[str, Any]) -> dict[str, Any]:
    api = config.get("manager-api") or config.get("manager_api") or {}
    return api if isinstance(api, dict) else {}


def manager_api_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = config if config is not None else gateway_runtime.config
    api = _manager_api(cfg)
    url = str(api.get("url") or "").strip()
    secret = str(api.get("secret") or "").strip()
    if not url or not secret:
        return False
    if "你" in secret or "placeholder" in secret.lower():
        return False
    if api.get("enabled") is False:
        return False
    # After control-admin merges server-base it sets read_config_from_api=True.
    if cfg.get("read_config_from_api") or api.get("enabled") is True:
        return True
    return False


def fetch_agent_models_sync(
    mac_address: str,
    client_id: str,
    selected_module: dict[str, Any] | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config if config is not None else gateway_runtime.config
    api = _manager_api(cfg)
    url = str(api.get("url") or "").rstrip("/")
    secret = str(api.get("secret") or "")
    selected = selected_module or (cfg.get("selected_module") or {})
    with httpx.Client(
        base_url=url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {secret}",
        },
        timeout=float(api.get("timeout") or 30),
        trust_env=False,
    ) as client:
        response = client.post(
            "/config/agent-models",
            json={
                "macAddress": mac_address,
                "clientId": client_id or mac_address,
                "selectedModule": selected,
            },
        )
        response.raise_for_status()
        result = response.json()
        code = result.get("code")
        if code == 10041:
            raise DeviceNotFound(str(result.get("msg") or "device not found"))
        if code == 10042:
            raise DeviceBindNeeded(str(result.get("msg") or ""))
        if code != 0:
            raise RuntimeError(result.get("msg") or f"agent-models error code={code}")
        data = result.get("data") or {}
        return data if isinstance(data, dict) else {}


def push_session_config_sync(
    pool: GrpcClientPool,
    *,
    client_id: str,
    device_id: str,
    private_config: dict[str, Any],
    reason: str = "ws_connect",
) -> str:
    message_id = uuid.uuid4().hex
    stub = audio_pb2_grpc.AgentServiceStub(pool.channel(AGENT_SERVICE))
    resp = stub.ApplySessionConfig(
        audio_pb2.ApplySessionConfigRequest(
            message_id=message_id,
            client_id=client_id,
            device_id=device_id or client_id,
            config_json=json.dumps(private_config, ensure_ascii=False),
            reason=reason,
        ),
        metadata=pool.metadata(client_id, message_id),
        timeout=15,
    )
    if int(resp.code) != 0:
        raise RuntimeError(resp.msg or "ApplySessionConfig failed")
    return resp.result or "ok"


def _slice_provider_config(
    private_config: dict[str, Any], *kinds: str
) -> dict[str, Any]:
    """Build a peer ApplyConfig payload with selected_module + provider blocks."""
    selected = private_config.get("selected_module") or {}
    out: dict[str, Any] = {"selected_module": {}}
    if isinstance(selected, dict):
        for kind in kinds:
            if selected.get(kind):
                out["selected_module"][kind] = selected[kind]
    for kind in kinds:
        block = private_config.get(kind)
        if isinstance(block, dict) and block:
            out[kind] = block
    return out


def push_peer_provider_config_sync(
    pool: GrpcClientPool,
    *,
    service_name: str,
    private_config: dict[str, Any],
    kinds: tuple[str, ...],
    reason: str = "agent_models",
) -> bool:
    """Push ASR/TTS/VAD slices from agent-models onto the owning microservice.

    Skips pushes whose selected provider has empty/placeholder credentials so a
    single device connect cannot wipe admin-filled keys and silence the fleet.
    """
    from xiaozhi_common.provider_aliases import (
        SECRET_CREDENTIAL_KEYS,
        credential_score,
        is_placeholder_credential,
        resolve_block,
    )

    payload = _slice_provider_config(private_config, *kinds)
    if not payload.get("selected_module") and not any(
        isinstance(payload.get(k), dict) for k in kinds
    ):
        return False
    for kind in kinds:
        selected = (payload.get("selected_module") or {}).get(kind) or ""
        if not selected:
            continue
        _name, _key, block = resolve_block(payload, kind, selected)
        # Local/stub providers often have no secret keys — allow those.
        # Cloud providers that *declare* secret fields must carry real values;
        # otherwise skip so we do not wipe admin-filled fleet credentials.
        declared = [k for k in SECRET_CREDENTIAL_KEYS if k in (block or {})]
        if declared and all(is_placeholder_credential(block.get(k)) for k in declared):
            logger.warning(
                f"Skip ApplyConfig to {service_name}: {kind}={selected} "
                f"has empty/placeholder credentials (reason={reason})"
            )
            return False
        if declared and credential_score(block) < 10:
            logger.warning(
                f"Skip ApplyConfig to {service_name}: {kind}={selected} "
                f"credentials unusable (reason={reason})"
            )
            return False
    message_id = uuid.uuid4().hex
    try:
        stub = admin_pb2_grpc.ConfigApplyServiceStub(pool.channel(service_name))
        resp = stub.ApplyConfig(
            admin_pb2.ApplyConfigRequest(
                message_id=message_id,
                config_json=json.dumps(payload, ensure_ascii=False),
                reason=reason,
            ),
            metadata=pool.metadata(message_id=message_id),
            timeout=15,
        )
        if int(resp.code) != 0:
            logger.warning(
                f"ApplyConfig to {service_name} failed: {resp.msg} kinds={kinds}"
            )
            return False
        selected = payload.get("selected_module") or {}
        logger.info(
            f"agent-models → {service_name} applied kinds={kinds} "
            f"selected={selected}"
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"ApplyConfig to {service_name} error: {exc}")
        return False


def speak_bind_prompt_sync(pool: GrpcClientPool, client_id: str, bind_code: str) -> None:
    code = "".join(ch for ch in str(bind_code) if ch.isdigit()) or str(bind_code)
    text = f"请登录控制面板，输入{code}，绑定设备。"
    message_id = uuid.uuid4().hex
    stub = audio_pb2_grpc.AudioSpeakerServiceStub(pool.channel(SPEAKER_SERVICE))
    stub.SpeakText(
        audio_pb2.SpeakRequest(
            message_id=message_id,
            client_id=client_id,
            text=text,
            emotion="neutral",
            index=1,
            total=1,
            end=True,
        ),
        metadata=pool.metadata(client_id, message_id),
        timeout=30,
    )


async def bind_device_on_connect(
    pool: GrpcClientPool,
    *,
    device_id: str,
    client_id: str,
    bind_id: str,
) -> dict[str, Any]:
    """On WS connect: pull agent-models and apply to agent + ASR/TTS/VAD peers.

    Returns status dict: {ok, need_bind, bind_code, llm, asr, error}.
    """
    out: dict[str, Any] = {
        "ok": False,
        "need_bind": False,
        "bind_code": "",
        "llm": "",
        "asr": "",
    }
    if not manager_api_enabled():
        out["ok"] = True
        out["skipped"] = True
        return out

    mac = device_id or bind_id
    try:
        private = await asyncio.to_thread(
            fetch_agent_models_sync, mac, client_id or mac
        )
    except DeviceBindNeeded as exc:
        out["need_bind"] = True
        out["bind_code"] = exc.bind_code
        connection_manager.update_meta(
            bind_id, need_bind=True, bind_code=exc.bind_code
        )
        try:
            await asyncio.to_thread(
                speak_bind_prompt_sync, pool, bind_id, exc.bind_code
            )
        except Exception as speak_exc:  # noqa: BLE001
            logger.warning(f"bind prompt TTS failed: {speak_exc}")
        logger.warning(f"Device need bind mac={mac} code={exc.bind_code}")
        return out
    except DeviceNotFound as exc:
        out["error"] = str(exc)
        connection_manager.update_meta(bind_id, need_bind=True, bind_code="")
        logger.warning(f"Device not found mac={mac}: {exc}")
        return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        logger.warning(f"agent-models pull failed mac={mac}: {exc}")
        return out

    selected = private.get("selected_module") or {}
    out["asr"] = str((selected or {}).get("ASR") or "")
    out["llm"] = str((selected or {}).get("LLM") or "")

    # Push provider configs to the services that actually run them.
    # (Previously only agent got ApplySessionConfig → ASR stayed StubASR.)
    await asyncio.to_thread(
        push_peer_provider_config_sync,
        pool,
        service_name=RECEIVER_SERVICE,
        private_config=private,
        kinds=("ASR",),
        reason=f"agent_models:{mac}",
    )
    await asyncio.to_thread(
        push_peer_provider_config_sync,
        pool,
        service_name=SPEAKER_SERVICE,
        private_config=private,
        kinds=("TTS",),
        reason=f"agent_models:{mac}",
    )
    await asyncio.to_thread(
        push_peer_provider_config_sync,
        pool,
        service_name=PREPROCESS_SERVICE,
        private_config=private,
        kinds=("VAD",),
        reason=f"agent_models:{mac}",
    )

    try:
        result = await asyncio.to_thread(
            push_session_config_sync,
            pool,
            client_id=bind_id,
            device_id=mac,
            private_config=private,
            reason="ws_connect",
        )
        out["ok"] = True
        out["result"] = result
        connection_manager.update_meta(
            bind_id, need_bind=False, bind_code="", agent_bound=True
        )
        logger.info(
            f"Device agent-models applied mac={mac} client={bind_id} "
            f"LLM={out['llm']} ASR={out['asr']}"
        )
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
        logger.warning(f"ApplySessionConfig failed mac={mac}: {exc}")
    return out
