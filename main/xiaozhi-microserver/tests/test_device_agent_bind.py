"""Unit tests for device agent-models bind helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

MICRO_ROOT = Path(__file__).resolve().parents[1]
COMMON = MICRO_ROOT / "common"
GENERATED = COMMON / "generated"
ACCESS_ROOT = MICRO_ROOT / "xiaozhi-access"
AGENT_ROOT = MICRO_ROOT / "xiaozhi-agent"


def _clear_app() -> None:
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]


def _pin_paths(*roots: Path, drop: tuple[Path, ...] = ()) -> None:
    """Put service roots at the front; drop sibling service apps that share `app`."""
    drop_resolved = {str(p.resolve()) for p in drop}
    for s in list(sys.path):
        if s in drop_resolved:
            try:
                sys.path.remove(s)
            except ValueError:
                pass
    for p in reversed(roots):
        s = str(p.resolve())
        if s in sys.path:
            sys.path.remove(s)
        sys.path.insert(0, s)
    _clear_app()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_manager_api_enabled_requires_url_secret():
    _pin_paths(COMMON, GENERATED, ACCESS_ROOT, drop=(AGENT_ROOT,))
    from app.ws.device_bind import manager_api_enabled

    assert not manager_api_enabled(
        {"read_config_from_api": True, "manager-api": {"url": "", "secret": ""}}
    )
    assert manager_api_enabled(
        {
            "read_config_from_api": True,
            "manager-api": {
                "url": "http://127.0.0.1:8002/xiaozhi",
                "secret": "test-secret",
            },
        }
    )


def test_fetch_agent_models_need_bind():
    _pin_paths(COMMON, GENERATED, ACCESS_ROOT, drop=(AGENT_ROOT,))
    from app.ws.device_bind import DeviceBindNeeded, fetch_agent_models_sync

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 10042, "msg": "123456"}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResp()

    cfg = {
        "manager-api": {
            "url": "http://127.0.0.1:8002/xiaozhi",
            "secret": "s",
        }
    }
    with patch("app.ws.device_bind.httpx.Client", return_value=FakeClient()):
        with pytest.raises(DeviceBindNeeded) as ei:
            fetch_agent_models_sync("aa:bb", "c1", config=cfg)
    assert ei.value.bind_code == "123456"


def test_apply_session_config_creates_llm_session():
    _pin_paths(COMMON, GENERATED, AGENT_ROOT, drop=(ACCESS_ROOT,))

    from xiaozhi_common.admin_config import deep_merge
    from xiaozhi_common.provider_aliases import mirror_provider_aliases
    from app.providers.llm import EchoLLM, create_llm
    from app.core.session import SessionStore

    base = {
        "selected_module": {"LLM": "EchoLLM"},
        "LLM": {"EchoLLM": {"type": "echo"}},
    }
    private = {
        "selected_module": {"LLM": "LLM_DoubaoLLM"},
        "LLM": {
            "LLM_DoubaoLLM": {
                "type": "openai",
                "api_key": "sk-test",
                "base_url": "https://example.com/v1",
                "model_name": "doubao",
            }
        },
        "prompt": "你是豆包助手",
    }
    merged = mirror_provider_aliases(deep_merge(base, private))
    llm = create_llm(merged)
    assert type(llm).__name__ == "OpenAICompatLLM"
    assert not isinstance(llm, EchoLLM)

    store = SessionStore()
    pool = MagicMock()
    session = store.apply_private_config(
        "dev-1",
        pool,
        merged,
        device_id="dev-1",
        prompt="你是豆包助手",
        intent_type="function_call",
        llm=llm,
    )
    assert session.llm is llm
    assert (session.config.get("selected_module") or {}).get("LLM")


def test_slice_provider_config_for_receiver():
    _pin_paths(COMMON, GENERATED, ACCESS_ROOT, drop=(AGENT_ROOT,))
    from app.ws.device_bind import _slice_provider_config

    private = {
        "selected_module": {
            "ASR": "ASR_DoubaoASR",
            "TTS": "TTS_DoubaoTTS",
            "LLM": "LLM_DoubaoLLM",
        },
        "ASR": {
            "ASR_DoubaoASR": {
                "type": "doubao",
                "appid": "1",
                "access_token": "t",
            }
        },
        "TTS": {"TTS_DoubaoTTS": {"type": "doubao"}},
        "LLM": {"LLM_DoubaoLLM": {"type": "openai"}},
    }
    sliced = _slice_provider_config(private, "ASR")
    assert sliced["selected_module"] == {"ASR": "ASR_DoubaoASR"}
    assert "ASR_DoubaoASR" in sliced["ASR"]
    assert "TTS" not in sliced
    assert "LLM" not in sliced


def test_push_peer_skips_placeholder_asr_credentials():
    _pin_paths(COMMON, GENERATED, ACCESS_ROOT, drop=(AGENT_ROOT,))
    from app.ws.device_bind import push_peer_provider_config_sync

    private = {
        "selected_module": {"ASR": "DoubaoASR"},
        "ASR": {
            "DoubaoASR": {
                "type": "doubao",
                "appid": "你的appid",
                "access_token": "你的token",
            }
        },
    }
    pool = MagicMock()
    assert (
        push_peer_provider_config_sync(
            pool,
            service_name="xiaozhi-audio-receiver",
            private_config=private,
            kinds=("ASR",),
            reason="test",
        )
        is False
    )
    pool.channel.assert_not_called()


def test_apply_remote_preserves_prior_secrets():
    _pin_paths(COMMON)
    from xiaozhi_common.runtime_config import ServiceRuntimeConfig

    cfg = ServiceRuntimeConfig.__new__(ServiceRuntimeConfig)
    cfg.service_root = Path(".")
    cfg.log_label = "Test"
    cfg.selected_kind = "ASR"
    cfg.path = Path("config.yaml")
    cfg.private_path = Path("data/.config.yaml")
    cfg._local = {
        "selected_module": {"ASR": "Doubao"},
        "ASR": {"Doubao": {"type": "doubao", "appid": "", "access_token": ""}},
    }
    cfg.data = {
        "selected_module": {"ASR": "Doubao"},
        "ASR": {
            "Doubao": {
                "type": "doubao",
                "appid": "real-app",
                "access_token": "real-token",
            }
        },
    }
    cfg.apply_remote(
        {
            "selected_module": {"ASR": "Doubao"},
            "ASR": {
                "Doubao": {
                    "type": "doubao",
                    "appid": "你的appid",
                    "access_token": "",
                }
            },
        },
        reason="device_bind",
    )
    block = cfg.data["ASR"]["Doubao"]
    assert block["appid"] == "real-app"
    assert block["access_token"] == "real-token"
