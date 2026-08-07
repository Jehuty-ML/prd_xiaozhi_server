import asyncio
import logging

import websockets
from config.logger import setup_logging


class SuppressInvalidHandshakeFilter(logging.Filter):
    """过滤掉无效握手错误日志（如HTTPS访问WS端口）"""

    def filter(self, record):
        msg = record.getMessage()
        suppress_keywords = [
            "opening handshake failed",
            "did not receive a valid HTTP request",
            "connection closed while reading HTTP request",
            "line without CRLF",
        ]
        return not any(keyword in msg for keyword in suppress_keywords)


def _setup_websockets_logger():
    """配置 websockets 相关的所有 logger，过滤无效握手错误"""
    filter_instance = SuppressInvalidHandshakeFilter()
    for logger_name in ["websockets", "websockets.server", "websockets.client"]:
        logger = logging.getLogger(logger_name)
        logger.addFilter(filter_instance)


_setup_websockets_logger()


from core.connection import ConnectionHandler
from core.connection_registry import (
    ConnectionLimits,
    ConnectionRegistry,
    ConnectionRejected,
)
from config.config_loader import get_config_from_api_async, reload_config_from_api
from core.auth import AuthManager, AuthenticationError
from core.utils.modules_initialize import initialize_modules
from core.utils.util import check_vad_update, check_asr_update
from core.utils import metrics as metrics_mod
from core.utils.session_state import resolve_session_mode
from core.utils.runtime_env import (
    allow_query_authorization,
    resolve_auth_enabled,
    resolve_environment,
)

TAG = __name__


class WebSocketServer:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging(config)
        metrics_mod.init_metrics(config)
        self.config_lock = asyncio.Lock()
        self.environment = resolve_environment(config)
        self.logger.bind(tag=TAG).info(f"运行环境: {self.environment}")
        modules = initialize_modules(
            self.logger,
            self.config,
            "VAD" in self.config["selected_module"],
            "ASR" in self.config["selected_module"],
            "LLM" in self.config["selected_module"],
            False,
            "Memory" in self.config["selected_module"],
            "Intent" in self.config["selected_module"],
        )
        self._vad = modules["vad"] if "vad" in modules else None
        self._asr = modules["asr"] if "asr" in modules else None
        self._llm = modules["llm"] if "llm" in modules else None
        self._intent = modules["intent"] if "intent" in modules else None
        self._memory = modules["memory"] if "memory" in modules else None

        auth_config = self.config["server"].get("auth", {}) or {}
        self.auth_enable = resolve_auth_enabled(self.config)
        # 设备白名单
        self.allowed_devices = set(auth_config.get("allowed_devices", []))
        secret_key = self.config["server"]["auth_key"]
        expire_seconds = auth_config.get("expire_seconds", None)
        self.auth = AuthManager(secret_key=secret_key, expire_seconds=expire_seconds)
        self.logger.bind(tag=TAG).info(
            f"连接认证: {'enabled' if self.auth_enable else 'disabled'} "
            f"(env={self.environment})"
        )

        self.connection_limits = ConnectionLimits.from_config(self.config["server"])
        self.connection_registry = ConnectionRegistry(self.connection_limits)
        # 在线 ConnectionHandler，供「通知更新配置」时广播 session_state.mode
        self.active_handlers: set = set()
        metrics_mod.set_ws_max_connections(self.connection_limits.max_connections)
        self.logger.bind(tag=TAG).info(
            f"连接硬上限: max={self.connection_limits.max_connections}, "
            f"per_device={self.connection_limits.max_connections_per_device}, "
            f"report_queue={self.connection_limits.report_queue_maxsize}"
        )
        self.logger.bind(tag=TAG).info(
            f"Prometheus指标: {'enabled' if metrics_mod.is_enabled() else 'disabled'}"
        )

    async def start(self):
        server_config = self.config["server"]
        host = server_config.get("ip", "0.0.0.0")
        port = int(server_config.get("port", 8000))

        async with websockets.serve(
            self._handle_connection, host, port, process_request=self._http_response
        ):
            await asyncio.Future()

    async def _handle_connection(self, websocket: websockets.ServerConnection):
        headers = dict(websocket.request.headers)
        if headers.get("device-id", None) is None:
            # 尝试从 URL 的查询参数中获取 device-id
            from urllib.parse import parse_qs, urlparse

            # 从 WebSocket 请求中获取路径
            request_path = websocket.request.path
            if not request_path:
                self.logger.bind(tag=TAG).error("无法获取请求路径")
                await websocket.close()
                return
            parsed_url = urlparse(request_path)
            query_params = parse_qs(parsed_url.query)
            if "device-id" not in query_params:
                await websocket.send("端口正常，如需测试连接，请启动digital-human测试")
                await websocket.close()
                return
            else:
                websocket.request.headers["device-id"] = query_params["device-id"][0]
            if "client-id" in query_params:
                websocket.request.headers["client-id"] = query_params["client-id"][0]
            if "authorization" in query_params:
                if allow_query_authorization(self.config):
                    websocket.request.headers["authorization"] = query_params[
                        "authorization"
                    ][0]
                    self.logger.bind(tag=TAG).warning(
                        "开发环境：已从 URL query 注入 authorization（生产环境将拒绝）"
                    )
                else:
                    self.logger.bind(tag=TAG).warning(
                        "生产环境禁止从 URL query 传递 authorization，请使用 Header"
                    )

        """处理新连接，每次创建独立的ConnectionHandler"""
        # 先认证，后建立连接
        try:
            await self._handle_auth(websocket)
        except AuthenticationError:
            await websocket.send("认证失败")
            await websocket.close()
            return

        device_id = dict(websocket.request.headers).get("device-id")
        # 先占名额，再创建重资源 Handler，避免超限仍初始化会话
        import uuid as _uuid

        session_id = str(_uuid.uuid4())
        acquired = False
        handler = None
        try:
            await self.connection_registry.try_acquire(session_id, device_id)
            acquired = True
            handler = ConnectionHandler(
                self.config,
                self._vad,
                self._asr,
                self._llm,
                self._memory,
                self._intent,
                self,  # 传入server实例
            )
            handler.session_id = session_id
            if hasattr(handler, "session_sm") and handler.session_sm:
                handler.session_sm.set_session_id(session_id)
            self.active_handlers.add(handler)
            self.logger.bind(tag=TAG).info(
                f"连接准入通过 device={device_id} session={handler.session_id} "
                f"active={self.connection_registry.active_count}/"
                f"{self.connection_limits.max_connections}"
            )
            await handler.handle_connection(websocket)
        except ConnectionRejected as e:
            self.logger.bind(tag=TAG).warning(
                f"连接被拒绝 device={device_id}: {e.reason} "
                f"active={self.connection_registry.active_count}"
            )
            try:
                # websockets reason 最长 123 字节
                await websocket.close(code=e.close_code, reason=e.reason[:120])
            except Exception:
                pass
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"处理连接时出错: {e}")
        finally:
            if handler is not None:
                self.active_handlers.discard(handler)
            if acquired:
                await self.connection_registry.release(session_id)
            # 强制关闭连接（如果还没有关闭的话）
            try:
                # 安全地检查WebSocket状态并关闭
                if hasattr(websocket, "closed") and not websocket.closed:
                    await websocket.close()
                elif hasattr(websocket, "state") and websocket.state.name != "CLOSED":
                    await websocket.close()
                else:
                    # 如果没有closed属性，直接尝试关闭
                    await websocket.close()
            except Exception as close_error:
                self.logger.bind(tag=TAG).error(
                    f"服务器端强制关闭连接时出错: {close_error}"
                )

    async def _http_response(self, websocket, request_headers):
        # 检查是否为 WebSocket 升级请求
        if request_headers.headers.get("connection", "").lower() == "upgrade":
            # 如果是 WebSocket 请求，返回 None 允许握手继续
            return None
        else:
            # 如果是普通 HTTP 请求，返回运行状态与连接水位
            body = (
                "Server is running\n"
                f"active_connections={self.connection_registry.active_count}\n"
                f"max_connections={self.connection_limits.max_connections}\n"
            )
            return websocket.respond(200, body)

    async def update_config(self) -> bool:
        """更新服务器配置并重新初始化组件

        Returns:
            bool: 更新是否成功
        """
        try:
            async with self.config_lock:
                # 重新读取 data/.config.yaml + 拉取智控台，避免沿用内存里的旧 connection
                try:
                    new_config = await reload_config_from_api()
                except RuntimeError:
                    new_config = await get_config_from_api_async(self.config)
                if new_config is None:
                    self.logger.bind(tag=TAG).error("获取新配置失败")
                    return False
                self.logger.bind(tag=TAG).info(f"获取新配置成功")
                conn_cfg = (new_config.get("server") or {}).get("connection") or {}
                self.logger.bind(tag=TAG).info(
                    f"连接硬上限将更新为: max={conn_cfg.get('max_connections')}, "
                    f"per_device={conn_cfg.get('max_connections_per_device')}"
                )
                # 检查 VAD 和 ASR 类型是否需要更新
                update_vad = check_vad_update(self.config, new_config)
                update_asr = check_asr_update(self.config, new_config)
                self.logger.bind(tag=TAG).info(
                    f"检查VAD和ASR类型是否需要更新: {update_vad} {update_asr}"
                )
                # mode 变化才打断在线会话；无关配置刷新不得 barge-in
                old_mode = resolve_session_mode(self.config)
                # 更新配置
                self.config = new_config
                self.environment = resolve_environment(new_config)
                self.logger.bind(tag=TAG).info(f"运行环境已同步: {self.environment}")
                # 同步连接硬上限（不影响已建立连接）
                self.connection_limits = ConnectionLimits.from_config(
                    self.config["server"]
                )
                self.connection_registry.limits = self.connection_limits
                metrics_mod.set_ws_max_connections(
                    self.connection_limits.max_connections
                )
                # 重新初始化组件
                modules = initialize_modules(
                    self.logger,
                    new_config,
                    update_vad,
                    update_asr,
                    "LLM" in new_config["selected_module"],
                    False,
                    "Memory" in new_config["selected_module"],
                    "Intent" in new_config["selected_module"],
                )

                # 更新组件实例
                if "vad" in modules:
                    self._vad = modules["vad"]
                if "asr" in modules:
                    self._asr = modules["asr"]
                if "llm" in modules:
                    self._llm = modules["llm"]
                if "intent" in modules:
                    self._intent = modules["intent"]
                if "memory" in modules:
                    self._memory = modules["memory"]
                # 仅当全局 session_state.mode 变化时广播并强制打断回 IDLE
                new_mode = resolve_session_mode(self.config)
                if new_mode != old_mode:
                    await self._apply_session_mode_to_handlers(new_mode)
                else:
                    self.logger.bind(tag=TAG).info(
                        f"session_state.mode 未变化 ({new_mode})，跳过在线打断"
                    )
                self.logger.bind(tag=TAG).info(f"更新配置任务执行完毕")
                return True
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"更新服务器配置失败: {str(e)}")
            return False

    async def _apply_session_mode_to_handlers(self, mode: str) -> None:
        """对所有在线连接热切换 session_state.mode（切换时强制打断）。"""
        handlers = list(getattr(self, "active_handlers", ()) or ())
        if not handlers:
            self.logger.bind(tag=TAG).info(
                f"session_state.mode={mode}（无在线连接，仅更新服务端配置）"
            )
            return
        self.logger.bind(tag=TAG).info(
            f"向 {len(handlers)} 个在线连接广播 session_state.mode={mode}"
        )
        for handler in handlers:
            try:
                await handler.apply_session_mode(mode, force_interrupt=True)
            except Exception as e:
                self.logger.bind(tag=TAG).warning(
                    f"应用 session mode 失败 session={getattr(handler, 'session_id', '-')}: {e}"
                )

    async def broadcast_speak(self, text: str, *, exclude=None) -> dict:
        """向本实例可播报的在线设备下发文案（打断后 TTS）。

        exclude: 通常为下发指令的管理台临时连接，不参与播报。
        """
        content = (text or "").strip()
        if not content:
            return {"ok": False, "matched": 0, "message": "text required"}

        exclude_id = id(exclude) if exclude is not None else None
        handlers = []
        for h in list(getattr(self, "active_handlers", ()) or ()):
            if exclude_id is not None and id(h) == exclude_id:
                continue
            if getattr(h, "_closed", False):
                continue
            # 管理台临时连接 / 未绑定设备通常没有 TTS，跳过
            if not getattr(h, "tts", None):
                continue
            if getattr(h, "need_bind", False):
                continue
            handlers.append(h)

        if not handlers:
            self.logger.bind(tag=TAG).info(
                "广播播报：无可用在线设备（需已绑定且 TTS 就绪）"
            )
            return {
                "ok": True,
                "matched": 0,
                "message": "no speakable online connection",
            }

        preview = content if len(content) <= 40 else content[:40] + "…"
        self.logger.bind(tag=TAG).info(
            f"向 {len(handlers)} 个在线连接广播播报 text={preview!r}"
        )
        applied = 0
        for handler in handlers:
            try:
                if await handler.speak_broadcast_text(content):
                    applied += 1
            except Exception as e:
                self.logger.bind(tag=TAG).warning(
                    f"广播播报失败 session={getattr(handler, 'session_id', '-')}: {e}"
                )
        return {
            "ok": applied > 0,
            "matched": applied,
            "message": "applied" if applied > 0 else "all speak attempts failed",
        }

    async def _handle_auth(self, websocket: websockets.ServerConnection):
        # 先认证，后建立连接
        if self.auth_enable:
            headers = dict(websocket.request.headers)
            device_id = headers.get("device-id", None)
            client_id = headers.get("client-id", None)
            if self.allowed_devices and device_id in self.allowed_devices:
                # 如果属于白名单内的设备，不校验token，直接放行
                return
            else:
                # 否则校验token
                token = headers.get("authorization", "")
                if token.startswith("Bearer "):
                    token = token[7:]  # 移除'Bearer '前缀
                else:
                    raise AuthenticationError("Missing or invalid Authorization header")
                # 进行认证
                auth_success = self.auth.verify_token(
                    token, client_id=client_id, username=device_id
                )
                if not auth_success:
                    raise AuthenticationError("Invalid token")
