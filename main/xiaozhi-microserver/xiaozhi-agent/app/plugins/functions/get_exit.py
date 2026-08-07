from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from app.tools.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from app.core.session import Session

handle_exit_intent_function_desc = {
    "type": "function",
    "function": {
        "name": "handle_exit_intent",
        "description": "当用户想结束对话或需要退出系统时调用",
        "parameters": {
            "type": "object",
            "properties": {
                "say_goodbye": {
                    "type": "string",
                    "description": "和用户友好结束对话的告别语",
                }
            },
            "required": ["say_goodbye"],
        },
    },
}


@register_function(
    "handle_exit_intent", handle_exit_intent_function_desc, ToolType.SYSTEM_CTL
)
def handle_exit_intent(session: "Session", say_goodbye: str | None = None):
    try:
        if say_goodbye is None:
            say_goodbye = "再见，祝您生活愉快！"
        session.close_after_chat = True
        try:
            session.request_close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"request_close failed: {exc}")
        logger.info(f"exit intent handled: {say_goodbye}")
        return ActionResponse(
            action=Action.RESPONSE, result="退出意图已处理", response=say_goodbye
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"handle_exit_intent error: {exc}")
        return ActionResponse(action=Action.NONE, result="退出意图处理失败", response="")
