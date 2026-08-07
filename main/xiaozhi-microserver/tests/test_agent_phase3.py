"""Phase-3 unit tests: Session API, EchoLLM tools, MCP/IoT device events."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1] / "xiaozhi-agent"
MICRO_ROOT = Path(__file__).resolve().parents[1]


def _use_agent_app() -> None:
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]
    paths = [
        str(AGENT_ROOT),
        str(MICRO_ROOT / "common"),
        str(MICRO_ROOT / "common" / "generated"),
    ]
    for s in paths:
        if s in sys.path:
            sys.path.remove(s)
    for s in reversed(paths):
        sys.path.insert(0, s)


_use_agent_app()


@pytest.fixture(autouse=True)
def _agent_path():
    _use_agent_app()
    yield


class _FakePool:
    def channel(self, _name):  # noqa: ANN001
        return None

    def metadata(self, *_a, **_k):  # noqa: ANN001
        return ()


@pytest.fixture()
def agent_cfg():
    from app.core.config_loader import runtime_config

    runtime_config.reload()
    return runtime_config.data


@pytest.fixture()
def session(agent_cfg):
    from app.core.session import Session

    spoken: list[str] = []
    sess = Session(
        client_id="unit-test",
        pool=_FakePool(),
        config=agent_cfg,
        prompt="unit",
        intent_type="function_call",
    )
    sess.speak = lambda text, emotion="neutral", index=0, total=0: spoken.append(text)
    sess.speak_end = lambda: None  # type: ignore[method-assign]
    sess._spoken = spoken  # type: ignore[attr-defined]
    return sess


@pytest.fixture()
def engine(agent_cfg):
    from app.core.chat_engine import ChatEngine
    from app.providers.llm import create_llm
    from app.providers.memory import create_memory
    from app.tools.handler import ToolHandler

    return ChatEngine(
        create_llm(agent_cfg),
        create_memory(agent_cfg),
        ToolHandler(agent_cfg),
        agent_cfg,
    )


def test_dialogue_memory_splice():
    from app.core.dialogue import Dialogue, Message

    d = Dialogue()
    d.put(Message(role="system", content="hi <memory></memory>"))
    d.put(Message(role="user", content="ping"))
    out = d.get_llm_dialogue_with_memory("remembered")
    assert out[0]["role"] == "system"
    assert "remembered" in out[0]["content"]
    assert out[-1]["content"] == "ping"


def test_echo_chat_plain(session, engine):
    reply = engine.chat(session, "hello-xiaozhi")
    assert "hello-xiaozhi" in (reply or "")
    assert any("hello-xiaozhi" in t for t in session._spoken)


def test_echo_get_time_tool(session, engine):
    reply = engine.chat(session, "现在几点了")
    assert reply
    assert "年" in reply or ":" in reply
    assert session._spoken


def test_echo_exit_intent(session, engine):
    reply = engine.chat(session, "再见")
    assert reply
    assert session.close_after_chat is True
    assert any("再见" in t for t in session._spoken)


def test_get_time_plugin_direct():
    from app.plugins.functions import get_time as mod
    from app.tools.register import Action

    resp = mod.get_time()
    assert resp.action == Action.REQLLM
    assert "年" in str(resp.result)


def test_handle_exit_plugin(session):
    from app.plugins.functions import get_exit as mod
    from app.tools.register import Action

    # Avoid gRPC close call in unit test
    session.request_close = lambda: None  # type: ignore[method-assign]
    resp = mod.handle_exit_intent(session, say_goodbye="拜拜啦")
    assert resp.action == Action.RESPONSE
    assert resp.response == "拜拜啦"
    assert session.close_after_chat is True


def test_mcp_tools_list_registers(session):
    from app.core.device_events import handle_device_event

    result = handle_device_event(
        session,
        "mcp",
        {
            "payload": {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {
                            "name": "self.light.turn_on",
                            "description": "Turn on",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            }
        },
    )
    assert result.startswith("mcp_tools=")
    assert "self_light_turn_on" in session.mcp_tools


def test_mcp_call_result_delivers_future(session):
    from concurrent.futures import Future

    from app.core.device_events import handle_device_event

    fut: Future = Future()
    session.mcp_call_futures[7] = fut
    result = handle_device_event(
        session,
        "mcp",
        {"payload": {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}},
    )
    assert result == "mcp_result_delivered"
    assert fut.result() == {"ok": True}


def test_iot_descriptors(session):
    from app.core.device_events import handle_device_event

    result = handle_device_event(
        session,
        "iot",
        {
            "descriptors": [
                {
                    "name": "Lamp",
                    "description": "desk",
                    "properties": {},
                    "methods": {},
                }
            ]
        },
    )
    assert result == "iot_descriptors=1"
    assert "Lamp" in session.iot_descriptors


def test_session_abort(session):
    from app.core.session import session_store
    from xiaozhi_common.session import SessionState

    session_store._sessions[session.client_id] = session
    assert session_store.abort(session.client_id, "unit") is True
    assert session.client_abort is True
    assert session.state == SessionState.ABORTED


def test_openai_falls_back_without_key(agent_cfg):
    from app.providers.llm import EchoLLM, create_llm

    cfg = dict(agent_cfg)
    cfg["selected_module"] = {**(cfg.get("selected_module") or {}), "LLM": "OpenAICompatLLM"}
    llm = create_llm(cfg)
    assert isinstance(llm, EchoLLM)


def test_system_prompt_renders(agent_cfg):
    from app.core.config_loader import runtime_config

    prompt = runtime_config.build_system_prompt()
    assert "小智" in prompt or "identity" in prompt.lower()
    assert "{{base_prompt}}" not in prompt
