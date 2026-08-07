"""Plugin registry — Session API (no conn)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Optional

from loguru import logger

TAG = __name__


class ToolType(Enum):
    NONE = (1, "none")
    WAIT = (2, "wait")
    CHANGE_SYS_PROMPT = (3, "change_sys_prompt")
    SYSTEM_CTL = (4, "system_ctl")
    IOT_CTL = (5, "iot_ctl")
    MCP_CLIENT = (6, "mcp_client")

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message


class Action(Enum):
    ERROR = (-1, "error")
    NOTFOUND = (0, "notfound")
    NONE = (1, "none")
    RESPONSE = (2, "response")
    REQLLM = (3, "reqllm")
    RECORD = (4, "record")

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message


class ActionResponse:
    def __init__(self, action: Action, result=None, response=None):
        self.action = action
        self.result = result
        self.response = response


class FunctionItem:
    def __init__(self, name: str, description: dict, func: Callable, type: ToolType):
        self.name = name
        self.description = description
        self.func = func
        self.type = type


all_function_registry: dict[str, FunctionItem] = {}


def register_function(name: str, desc: dict, type: ToolType | None = None):
    def decorator(func: Callable):
        all_function_registry[name] = FunctionItem(
            name, desc, func, type or ToolType.WAIT
        )
        logger.debug(f"plugin registered: {name}")
        return func

    return decorator


def get_function(name: str) -> Optional[FunctionItem]:
    return all_function_registry.get(name)
