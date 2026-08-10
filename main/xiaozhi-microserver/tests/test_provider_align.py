"""Provider dual-name aliases + factory routing (FunASR/Doubao/ChatGLM)."""

from __future__ import annotations

import sys
from pathlib import Path

MICRO_ROOT = Path(__file__).resolve().parents[1]
RECEIVER_ROOT = MICRO_ROOT / "xiaozhi-audio-receiver"
SPEAKER_ROOT = MICRO_ROOT / "xiaozhi-audio-speaker"
AGENT_ROOT = MICRO_ROOT / "xiaozhi-agent"
ADMIN_ROOT = MICRO_ROOT / "xiaozhi-control-admin"


def _clear_app() -> None:
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]


def _prep(service_root: Path) -> None:
    _clear_app()
    paths = [
        str(service_root),
        str(MICRO_ROOT / "common"),
        str(MICRO_ROOT / "common" / "generated"),
    ]
    for s in list(sys.path):
        if "xiaozhi-" in s.replace("\\", "/") and s not in paths:
            if any(
                x in s.replace("\\", "/")
                for x in (
                    "xiaozhi-audio-",
                    "xiaozhi-agent",
                    "xiaozhi-control-admin",
                    "xiaozhi-access",
                )
            ):
                try:
                    sys.path.remove(s)
                except ValueError:
                    pass
    for s in reversed(paths):
        if s not in sys.path:
            sys.path.insert(0, s)


def test_alias_resolve_and_mirror():
    sys.path.insert(0, str(MICRO_ROOT / "common"))
    from xiaozhi_common.provider_aliases import (
        mirror_provider_aliases,
        resolve_block,
    )

    cfg = {
        "selected_module": {"ASR": "DoubaoASR", "TTS": "DoubaoTTS", "LLM": "DoubaoLLM"},
        "ASR": {
            "DoubaoASR": {
                "type": "doubao",
                "appid": "1",
                "access_token": "t",
            }
        },
        "TTS": {
            "Doubao": {
                "type": "doubao",
                "access_token": "t",
            }
        },
        "LLM": {
            "DoubaoLLM": {
                "type": "openai",
                "api_key": "k",
                "base_url": "https://example.com",
            }
        },
    }
    selected, key, block = resolve_block(cfg, "ASR")
    assert selected == "DoubaoASR"
    assert key == "DoubaoASR"
    assert block["type"] == "doubao"

    selected, key, block = resolve_block(cfg, "TTS", "DoubaoTTS")
    assert key == "Doubao"
    assert block["type"] == "doubao"

    mirrored = mirror_provider_aliases(dict(cfg))
    assert mirrored["selected_module"]["ASR"] == "Doubao"
    assert mirrored["selected_module"]["TTS"] == "Doubao"
    assert mirrored["selected_module"]["LLM"] == "Doubao"
    assert "Doubao" in mirrored["ASR"] and "DoubaoASR" in mirrored["ASR"]
    assert "DoubaoTTS" in mirrored["TTS"]
    assert "Doubao" in mirrored["LLM"]


def test_llm_prefers_manager_api_key_over_local_placeholder():
    """Local Doubao api_key='' must not beat LLM_DoubaoLLM from 智控台."""
    sys.path.insert(0, str(MICRO_ROOT / "common"))
    from xiaozhi_common.provider_aliases import (
        mirror_provider_aliases,
        resolve_block,
    )

    cfg = {
        "selected_module": {"LLM": "LLM_DoubaoLLM"},
        "LLM": {
            "Doubao": {
                "type": "openai",
                "api_key": "",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "model_name": "doubao-1-5-pro-32k-250115",
            },
            "DoubaoLLM": {
                "type": "openai",
                "api_key": "",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "model_name": "doubao-1-5-pro-32k-250115",
            },
            "LLM_DoubaoLLM": {
                "type": "openai",
                "api_key": "61d3-real-key",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "model_name": "doubao-1-5-pro-32k-250115",
            },
        },
    }
    mirrored = mirror_provider_aliases(dict(cfg))
    assert mirrored["selected_module"]["LLM"] == "Doubao"
    assert mirrored["LLM"]["Doubao"]["api_key"] == "61d3-real-key"
    selected, key, block = resolve_block(mirrored, "LLM")
    assert selected == "Doubao"
    assert block["api_key"] == "61d3-real-key"
    assert key in ("Doubao", "DoubaoLLM", "LLM_DoubaoLLM")


def test_asr_factory_doubao_missing_creds_falls_back():
    _prep(RECEIVER_ROOT)
    from app.providers.asr import create_asr
    from app.providers.asr.stub import StubASR

    asr = create_asr(
        {
            "selected_module": {"ASR": "Doubao"},
            "ASR": {
                "Doubao": {"type": "doubao", "appid": "", "access_token": ""},
            },
        }
    )
    assert isinstance(asr, StubASR)

    asr2 = create_asr(
        {
            "selected_module": {"ASR": "DoubaoASR"},
            "ASR": {
                "DoubaoASR": {
                    "type": "doubao",
                    "appid": "你的appid",
                    "access_token": "你的token",
                },
            },
        }
    )
    assert isinstance(asr2, StubASR)


def test_asr_factory_funasr_missing_dep_falls_back():
    _prep(RECEIVER_ROOT)
    from app.providers.asr import create_asr
    from app.providers.asr.stub import StubASR

    asr = create_asr(
        {
            "selected_module": {"ASR": "FunASR"},
            "ASR": {
                "FunASR": {
                    "type": "fun_local",
                    "model_dir": "/nonexistent/SenseVoiceSmall",
                },
            },
        }
    )
    # Missing funasr or model → StubASR
    assert isinstance(asr, StubASR)


def test_tts_factory_doubao_dual_name_missing_token():
    _prep(SPEAKER_ROOT)
    from app.providers.tts import create_tts
    from app.providers.tts.echo import EchoTTS

    for name in ("Doubao", "DoubaoTTS"):
        tts = create_tts(
            {
                "selected_module": {"TTS": name},
                "TTS": {
                    name: {"type": "doubao", "access_token": ""},
                },
            }
        )
        assert isinstance(tts, EchoTTS), name


def test_llm_factory_chatglm_doubao_aliases_need_key():
    _prep(AGENT_ROOT)
    from app.providers.llm import EchoLLM, create_llm

    for name in ("Doubao", "DoubaoLLM", "ChatGLM", "ChatGLMLLM"):
        llm = create_llm(
            {
                "selected_module": {"LLM": name},
                "LLM": {
                    name: {
                        "type": "openai",
                        "api_key": "",
                        "model_name": "x",
                        "url": "https://example.com",
                    },
                },
            }
        )
        assert isinstance(llm, EchoLLM), name


def test_llm_factory_unsupported_type_raises():
    _prep(AGENT_ROOT)
    from app.providers.llm import create_llm
    import pytest

    with pytest.raises(ValueError, match="当前还不支持"):
        create_llm(
            {
                "selected_module": {"LLM": "Dify"},
                "LLM": {"Dify": {"type": "dify", "api_key": "k"}},
            }
        )


def test_merge_api_config_mirrors_aliases():
    _prep(ADMIN_ROOT)
    from app.core.handler.config_store import merge_api_config

    local = {
        "manager-api": {"url": "http://x", "secret": "s", "enabled": True},
        "server": {"port": 8000},
    }
    api = {
        "selected_module": {"ASR": "DoubaoASR", "TTS": "DoubaoTTS", "LLM": "ChatGLMLLM"},
        "ASR": {"DoubaoASR": {"type": "doubao", "appid": "1", "access_token": "t"}},
        "TTS": {"DoubaoTTS": {"type": "doubao", "access_token": "t"}},
        "LLM": {
            "ChatGLMLLM": {
                "type": "openai",
                "api_key": "k",
                "url": "https://open.bigmodel.cn/api/paas/v4/",
            }
        },
        "server": {},
    }
    merged = merge_api_config(local, api)
    assert merged["selected_module"]["ASR"] == "Doubao"
    assert merged["selected_module"]["TTS"] == "Doubao"
    assert merged["selected_module"]["LLM"] == "ChatGLM"
    assert merged["ASR"]["Doubao"]["type"] == "doubao"
    assert merged["TTS"]["Doubao"]["type"] == "doubao"
    assert merged["LLM"]["ChatGLM"]["type"] == "openai"
