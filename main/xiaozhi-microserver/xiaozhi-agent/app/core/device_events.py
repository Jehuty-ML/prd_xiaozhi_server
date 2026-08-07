"""Handle device MCP / IoT events forwarded from access."""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from app.core.session import Session


def _sanitize_tool_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name or "tool")[:64]


def handle_device_event(session: Session, event_type: str, payload: dict[str, Any]) -> str:
    event_type = (event_type or "").lower()
    if event_type == "mcp":
        return _handle_mcp(session, payload)
    if event_type == "iot":
        return _handle_iot(session, payload)
    return f"unknown event_type={event_type}"


def _handle_mcp(session: Session, payload: dict[str, Any]) -> str:
    """Accept MCP JSON-RPC style payloads from device."""
    # Device may wrap as {"payload": {...}} or raw jsonrpc
    body = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    if not isinstance(body, dict):
        return "invalid mcp payload"

    # tools/list result (has tools) — register before treating as call response
    result = body.get("result")
    if isinstance(result, dict) and "tools" in result:
        for tool in result.get("tools") or []:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            sanitized = _sanitize_tool_name(tool["name"])
            session.mcp_tools[sanitized] = tool
            session.mcp_name_mapping[sanitized] = tool["name"]
        logger.info(
            f"MCP tools registered client={session.client_id} "
            f"count={len(session.mcp_tools)}"
        )
        return f"mcp_tools={len(session.mcp_tools)}"

    # Response to tools/call
    if "id" in body and ("result" in body or "error" in body):
        call_id = body.get("id")
        fut = session.mcp_call_futures.get(call_id)
        if fut and not fut.done():
            if "error" in body:
                fut.set_exception(RuntimeError(str(body["error"])))
            else:
                fut.set_result(body.get("result"))
            return "mcp_result_delivered"
        return "mcp_result_no_waiter"

    method = body.get("method") or ""
    params = body.get("params") or {}

    if method in ("notifications/initialized", "initialized"):
        return "mcp_initialized"

    # Device announcing tools directly
    if method == "tools/list_changed" or "tools" in params:
        tools = params.get("tools") or []
        for tool in tools:
            if not isinstance(tool, dict) or not tool.get("name"):
                continue
            sanitized = _sanitize_tool_name(tool["name"])
            session.mcp_tools[sanitized] = tool
            session.mcp_name_mapping[sanitized] = tool["name"]
        return f"mcp_tools={len(session.mcp_tools)}"

    logger.debug(f"MCP event method={method} client={session.client_id}")
    return f"mcp_ack:{method or 'message'}"


def _handle_iot(session: Session, payload: dict[str, Any]) -> str:
    descriptors = payload.get("descriptors")
    if isinstance(descriptors, list):
        for desc in descriptors:
            if not isinstance(desc, dict) or not desc.get("name"):
                continue
            session.iot_descriptors[desc["name"]] = desc
        logger.info(
            f"IoT descriptors client={session.client_id} "
            f"count={len(session.iot_descriptors)}"
        )
        return f"iot_descriptors={len(session.iot_descriptors)}"

    states = payload.get("states") or payload.get("update")
    if isinstance(states, list):
        for state in states:
            if not isinstance(state, dict):
                continue
            name = state.get("name")
            if name and name in session.iot_descriptors:
                session.iot_descriptors[name]["state"] = state.get("state") or state
        return "iot_states_updated"

    # Pass-through commands already handled by device; just ack
    return "iot_ack"
