import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.utils.session_state import SessionEvent

TAG = __name__


async def handleAbortMessage(conn: "ConnectionHandler"):
    conn.logger.bind(tag=TAG).info("Abort message received")
    # 任意主状态均可打断回 IDLE；转移失败则跳过副作用
    if not conn.transition_session(SessionEvent.ABORT, detail="client"):
        return
    # 设置成打断状态，会自动打断llm、tts任务
    conn.close_after_chat = False
    conn.client_abort = True
    conn.clear_queues()
    # 打断客户端说话状态
    await conn.websocket.send(
        json.dumps({"type": "tts", "state": "stop", "session_id": conn.session_id})
    )
    # 强制结束广播会话：即使 sentence_id 已被嵌套 TTS 改写
    conn.clearSpeakStatus(end_broadcast=True)
    conn.logger.bind(tag=TAG).info("Abort message received-end")
