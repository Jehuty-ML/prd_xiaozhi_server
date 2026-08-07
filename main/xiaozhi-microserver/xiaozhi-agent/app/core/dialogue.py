"""Dialogue history for LLM (ported from monolith core/utils/dialogue.py)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Dict, List, Optional


class Message:
    def __init__(
        self,
        role: str,
        content: str = None,
        uniq_id: str = None,
        tool_calls=None,
        tool_call_id=None,
        is_temporary: bool = False,
    ):
        self.uniq_id = uniq_id if uniq_id is not None else str(uuid.uuid4())
        self.role = role
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_id = tool_call_id
        self.is_temporary = is_temporary


class Dialogue:
    def __init__(self) -> None:
        self.dialogue: List[Message] = []
        self.current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def put(self, message: Message) -> None:
        self.dialogue.append(message)

    def getMessages(self, m: Message, dialogue: list) -> None:
        if m.tool_calls is not None:
            dialogue.append({"role": m.role, "tool_calls": m.tool_calls})
        elif m.role == "tool":
            dialogue.append(
                {
                    "role": m.role,
                    "tool_call_id": (
                        str(uuid.uuid4()) if m.tool_call_id is None else m.tool_call_id
                    ),
                    "content": m.content,
                }
            )
        else:
            dialogue.append({"role": m.role, "content": m.content})

    def get_llm_dialogue(self) -> List[Dict[str, str]]:
        return self.get_llm_dialogue_with_memory(None)

    def update_system_message(self, new_content: str) -> None:
        system_msg = next((msg for msg in self.dialogue if msg.role == "system"), None)
        if system_msg:
            system_msg.content = new_content
        else:
            self.put(Message(role="system", content=new_content))

    def _ensure_tool_calls_complete(self, messages: List[Message]) -> List[Message]:
        pending_tool_calls = set()
        result: List[Message] = []
        for msg in messages:
            result.append(msg)
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if tc_id:
                        pending_tool_calls.add(tc_id)
            elif msg.role == "tool" and msg.tool_call_id:
                pending_tool_calls.discard(msg.tool_call_id)
        for missing_id in pending_tool_calls:
            result.append(
                Message(
                    role="tool",
                    content='{"status": "interrupted", "message": "动作已取消/被打断"}',
                    tool_call_id=missing_id,
                )
            )
        return result

    def get_llm_dialogue_with_memory(
        self, memory_str: Optional[str] = None
    ) -> List[Dict[str, str]]:
        dialogue: List[Dict] = []
        system_message = next(
            (msg for msg in self.dialogue if msg.role == "system"), None
        )
        if system_message:
            full_prompt = system_message.content or ""
            full_prompt = full_prompt.replace(
                "{{current_time}}", datetime.now().strftime("%H:%M")
            )
            if memory_str is not None:
                full_prompt = re.sub(
                    r"<memory>.*?</memory>",
                    f"<memory>\n{memory_str}\n</memory>",
                    full_prompt,
                    flags=re.DOTALL,
                )
            dialogue.append({"role": "system", "content": full_prompt})

        non_system = [m for m in self.dialogue if m.role != "system"]
        fewshot = [m for m in non_system if m.is_temporary]
        actual = [m for m in non_system if not m.is_temporary]
        for m in self._ensure_tool_calls_complete(fewshot):
            self.getMessages(m, dialogue)
        for m in self._ensure_tool_calls_complete(actual):
            self.getMessages(m, dialogue)
        return dialogue
