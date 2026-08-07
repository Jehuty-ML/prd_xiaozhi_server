"""Unified tool handler bound to Session (not ConnectionHandler)."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Dict, List, Optional

from loguru import logger

from app.providers.intent import intent_functions
from app.tools.loadplugins import auto_import_modules
from app.tools.register import Action, ActionResponse, ToolType, all_function_registry, get_function


class ToolHandler:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._ready = False

    def ensure_loaded(self) -> None:
        if self._ready:
            return
        auto_import_modules("app.plugins.functions")
        self._ready = True
        logger.info(
            f"plugins loaded: {sorted(all_function_registry.keys())}"
        )

    def get_functions(self) -> List[dict]:
        self.ensure_loaded()
        names = intent_functions(self.config)
        descs = []
        for name in names:
            item = get_function(name)
            if item:
                descs.append(item.description)
        # Device MCP tools registered on session are merged at chat time
        return descs

    def get_functions_for_session(self, session) -> List[dict]:  # noqa: ANN001
        tools = list(self.get_functions())
        for _name, tool_data in (session.mcp_tools or {}).items():
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": _name,
                        "description": tool_data.get("description") or _name,
                        "parameters": {
                            "type": tool_data.get("inputSchema", {}).get("type", "object"),
                            "properties": tool_data.get("inputSchema", {}).get(
                                "properties", {}
                            ),
                            "required": tool_data.get("inputSchema", {}).get(
                                "required", []
                            ),
                        },
                    },
                }
            )
        return tools

    async def execute(
        self, session, tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        self.ensure_loaded()
        # Device MCP tool?
        if tool_name in (session.mcp_tools or {}):
            return await self._call_device_mcp(session, tool_name, arguments)

        item = get_function(tool_name)
        if not item:
            return ActionResponse(
                action=Action.NOTFOUND, response=f"工具 {tool_name} 不存在"
            )
        try:
            kwargs = dict(arguments or {})
            needs_session = item.type.code in (
                ToolType.SYSTEM_CTL.code,
                ToolType.IOT_CTL.code,
                ToolType.CHANGE_SYS_PROMPT.code,
            )
            # Also inject session if first param is named session
            sig = inspect.signature(item.func)
            params = list(sig.parameters.values())
            if needs_session or (params and params[0].name == "session"):
                result = item.func(session, **kwargs)
            else:
                result = item.func(**kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"tool {tool_name} failed: {exc}")
            return ActionResponse(action=Action.ERROR, response=str(exc))

    async def handle_llm_function_call(
        self, session, function_call_data: Dict[str, Any]
    ) -> Optional[ActionResponse]:
        name = function_call_data.get("name")
        arguments = function_call_data.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                return ActionResponse(action=Action.ERROR, response="无法解析函数参数")
        logger.info(f"tool call name={name} args={arguments}")
        return await self.execute(session, name, arguments)

    async def _call_device_mcp(
        self, session, tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        import uuid
        from concurrent.futures import Future

        real_name = session.mcp_name_mapping.get(tool_name, tool_name)
        call_id = session.mcp_next_id
        session.mcp_next_id += 1
        fut: Future = Future()
        session.mcp_call_futures[call_id] = fut
        payload = {
            "type": "mcp",
            "payload": {
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": real_name, "arguments": arguments or {}},
            },
        }
        ok = session.send_device_json(payload)
        if not ok:
            session.mcp_call_futures.pop(call_id, None)
            return ActionResponse(
                action=Action.ERROR, response="无法将 MCP 调用发往设备"
            )
        try:
            # Blocking wait in thread pool context — chat engine uses to_thread/result
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: fut.result(timeout=int(session.config.get("tool_call_timeout", 30)))
            )
            return ActionResponse(action=Action.REQLLM, result=str(result), response=None)
        except Exception as exc:  # noqa: BLE001
            return ActionResponse(action=Action.ERROR, response=str(exc))
        finally:
            session.mcp_call_futures.pop(call_id, None)
