"""Phase-3 chat engine: LLM stream → SpeakText sentences + tool loop."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from typing import Any, List, Optional

from loguru import logger

from app.core.dialogue import Message
from app.core.session import Session
from app.providers.llm import LLMProviderBase
from app.providers.memory import MemoryProviderBase
from app.tools.handler import ToolHandler
from app.tools.register import Action, ActionResponse
from xiaozhi_common.mq import ChatHistoryPublisher
from xiaozhi_common.resilience import (
    UpstreamError,
    UpstreamKind,
    call_with_resilience,
    get_fallback_text,
    timeout_for_stage,
)

_SENTENCE_END = re.compile(r"([。！？!?；;\n])")


def _merge_tool_calls(tool_calls_list: list, tools_call) -> None:
    """Merge OpenAI-style streaming tool_call deltas into tool_calls_list."""
    for tc in tools_call:
        idx = getattr(tc, "index", None)
        if idx is None and isinstance(tc, dict):
            idx = tc.get("index", 0)
        idx = int(idx or 0)
        while len(tool_calls_list) <= idx:
            tool_calls_list.append({"id": "", "name": "", "arguments": ""})
        slot = tool_calls_list[idx]
        tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else None)
        if tc_id:
            slot["id"] = tc_id
        fn = getattr(tc, "function", None) or (tc.get("function") if isinstance(tc, dict) else None)
        if fn is not None:
            name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)
            args = getattr(fn, "arguments", None) or (
                fn.get("arguments") if isinstance(fn, dict) else None
            )
            if name:
                slot["name"] = name
            if args:
                slot["arguments"] = (slot.get("arguments") or "") + str(args)
        if not slot["id"]:
            slot["id"] = uuid.uuid4().hex


class SentenceSpeaker:
    """Buffer streamed tokens and flush complete sentences via Session.speak."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.buf = ""
        self.parts: List[str] = []
        self.index = 0

    def feed(self, chunk: str) -> None:
        if not chunk or self.session.client_abort:
            return
        self.buf += chunk
        while True:
            m = _SENTENCE_END.search(self.buf)
            if not m:
                break
            end = m.end()
            sentence = self.buf[:end].strip()
            self.buf = self.buf[end:]
            if sentence:
                self._emit(sentence)

    def flush(self) -> None:
        rem = self.buf.strip()
        self.buf = ""
        if rem and not self.session.client_abort:
            self._emit(rem)

    def _emit(self, text: str) -> None:
        self.index += 1
        self.parts.append(text)
        self.session.speak(text, index=self.index, total=0)

    @property
    def full_text(self) -> str:
        return "".join(self.parts)


class ChatEngine:
    def __init__(
        self,
        llm: LLMProviderBase,
        memory: MemoryProviderBase,
        tools: ToolHandler,
        config: dict[str, Any],
    ) -> None:
        self.llm = llm
        self.memory = memory
        self.tools = tools
        self.config = config
        self._chat_history = ChatHistoryPublisher.from_config(config)
        # gRPC workers are multi-threaded; never share one asyncio loop across them.
        self._async_lock = threading.Lock()

    def _run_async(self, coro):  # noqa: ANN001
        with self._async_lock:
            return asyncio.run(coro)

    def _publish_history(self, session: Session, chat_type: int, content: str) -> None:
        try:
            self._chat_history.publish(
                mac_address=session.device_id or session.client_id,
                session_id=session.session_id,
                chat_type=chat_type,
                content=content or "",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"chat history publish skipped: {exc}")

    def chat(self, session: Session, query: Optional[str] = None, depth: int = 0) -> str:
        if depth == 0:
            session.reset_abort()
            session.sentence_id = uuid.uuid4().hex
            session._speak_index = 0
            session.dialogue.put(Message(role="user", content=query or ""))
            if query:
                self._publish_history(session, 1, query)

        max_depth = int(self.config.get("max_tool_depth", 5))
        force_final = depth >= max_depth
        if force_final:
            session.dialogue.put(
                Message(
                    role="user",
                    content="[系统提示] 已达到最大工具调用次数，请基于已有信息直接回答，不要再调用工具。",
                )
            )

        memory_str = ""
        if query and depth == 0:
            try:
                memory_str = self._run_async(self.memory.query_memory(query))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"memory query failed: {exc}")

        dialogue = session.dialogue.get_llm_dialogue_with_memory(memory_str or None)
        functions = None
        if session.intent_type == "function_call" and not force_final:
            functions = self.tools.get_functions_for_session(session)

        speaker = SentenceSpeaker(session)
        tool_calls_list: list = []
        tool_call_flag = False
        content_arguments = ""
        response_chunks: List[str] = []

        try:
            def _consume():
                nonlocal tool_call_flag, content_arguments
                llm = session.llm or self.llm
                if functions:
                    stream = llm.response_with_functions(
                        session.session_id, dialogue, functions
                    )
                    for content, tools_call in stream:
                        if session.client_abort:
                            break
                        if content:
                            content_arguments += content
                            if not tool_call_flag:
                                response_chunks.append(content)
                                speaker.feed(content)
                        if tools_call:
                            tool_call_flag = True
                            _merge_tool_calls(tool_calls_list, tools_call)
                else:
                    for content in llm.response(session.session_id, dialogue):
                        if session.client_abort:
                            break
                        if content:
                            response_chunks.append(content)
                            speaker.feed(content)

            active_llm = session.llm or self.llm
            cfg = session.config or self.config
            # Wall-clock cap so a hung upstream still yields fallback speech.
            llm_timeout = timeout_for_stage("llm", cfg)
            call_with_resilience(
                "llm",
                _consume,
                config=cfg,
                provider=type(active_llm).__name__,
                max_retries=0,
                timeout_seconds=llm_timeout,
            )
        except UpstreamError as exc:
            logger.warning(f"LLM upstream failed: {exc}")
            try:
                from xiaozhi_common import metrics as metrics_mod

                metrics_mod.observe_degraded("llm", exc.kind.value)
            except Exception:  # noqa: BLE001
                pass
            # Stop a late stream thread from talking over the fallback.
            session.mark_abort("llm_upstream")
            fallback = get_fallback_text(session.config or self.config, "llm", exc.kind)
            session.reset_abort()
            speaker.feed(fallback)
            speaker.flush()
            try:
                session.speak_end()
            except Exception:  # noqa: BLE001
                pass
            session.mark_abort("llm_upstream")
            session.dialogue.put(Message(role="assistant", content=fallback))
            if depth == 0:
                self._publish_history(session, 2, fallback)
            return fallback
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"LLM failed: {exc}")
            session.mark_abort("llm_failed")
            fallback = get_fallback_text(
                session.config or self.config, "llm", UpstreamKind.UNAVAILABLE
            )
            session.reset_abort()
            speaker.feed(fallback)
            speaker.flush()
            try:
                session.speak_end()
            except Exception:  # noqa: BLE001
                pass
            session.mark_abort("llm_failed")
            session.dialogue.put(Message(role="assistant", content=fallback))
            if depth == 0:
                self._publish_history(session, 2, fallback)
            return fallback

        # Text-based <tool_call> fallback
        if not tool_calls_list and content_arguments.startswith("<tool_call>"):
            tool_call_flag = True
            try:
                raw = content_arguments.replace("<tool_call>", "").replace(
                    "</tool_call>", ""
                )
                data = json.loads(raw)
                tool_calls_list.append(
                    {
                        "id": uuid.uuid4().hex,
                        "name": data["name"],
                        "arguments": json.dumps(
                            data.get("arguments") or {}, ensure_ascii=False
                        ),
                    }
                )
            except Exception:  # noqa: BLE001
                tool_call_flag = False

        if tool_call_flag and tool_calls_list and not session.client_abort:
            # Discard partial spoken text when switching to tools (echo may have spoken nothing)
            if response_chunks and any(tc.get("name") for tc in tool_calls_list):
                # Keep streamed assistant text in history if any
                streamed = "".join(response_chunks).strip()
                if streamed and not any(
                    tc.get("name") and not streamed.startswith("<")
                    for tc in tool_calls_list
                ):
                    pass

            tool_results = []
            for tc in tool_calls_list:
                if not tc.get("name"):
                    continue
                logger.info(f"exec tool {tc['name']} args={tc.get('arguments')}")
                result = self._run_async(
                    self.tools.handle_llm_function_call(session, tc)
                )
                tool_results.append((result, tc))

            return self._handle_tool_results(
                session, query, tool_results, depth, speaker
            )

        speaker.flush()
        if not session.client_abort:
            session.speak_end()
        text = speaker.full_text or "".join(response_chunks)
        if text:
            session.dialogue.put(Message(role="assistant", content=text))
            if depth == 0:
                self._publish_history(session, 2, text)
        return text

    def _handle_tool_results(
        self,
        session: Session,
        query: str,
        tool_results: list,
        depth: int,
        speaker: SentenceSpeaker,
    ) -> str:
        need_llm = False
        direct_parts: List[str] = []

        for result, tc in tool_results:
            if result is None:
                continue
            # Record tool call + result in dialogue
            session.dialogue.put(
                Message(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        {
                            "id": tc.get("id") or uuid.uuid4().hex,
                            "type": "function",
                            "function": {
                                "name": tc.get("name"),
                                "arguments": tc.get("arguments") or "{}",
                            },
                        }
                    ],
                )
            )
            tool_content = ""
            if result.action in (Action.RESPONSE, Action.ERROR, Action.NOTFOUND):
                text = result.response if result.response else result.result
                if text:
                    direct_parts.append(str(text))
                    speaker.feed(str(text))
                tool_content = str(result.result or result.response or "")
            elif result.action == Action.REQLLM:
                need_llm = True
                tool_content = str(result.result or "")
            else:
                tool_content = str(result.result or result.response or "")

            session.dialogue.put(
                Message(
                    role="tool",
                    content=tool_content,
                    tool_call_id=tc.get("id"),
                )
            )

        if direct_parts and not need_llm:
            speaker.flush()
            if not session.client_abort:
                session.speak_end()
            text = "".join(direct_parts)
            session.dialogue.put(Message(role="assistant", content=text))
            if depth == 0:
                self._publish_history(session, 2, text)
            return text

        if need_llm:
            reply = self.chat(session, query=None, depth=depth + 1)
            if depth == 0 and reply:
                self._publish_history(session, 2, reply)
            return reply

        speaker.flush()
        if not session.client_abort:
            session.speak_end()
        text = speaker.full_text or ""
        if depth == 0 and text:
            self._publish_history(session, 2, text)
        return text
