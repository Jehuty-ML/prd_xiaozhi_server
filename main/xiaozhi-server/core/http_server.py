import asyncio
from aiohttp import web
from config.logger import setup_logging
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.utils import metrics as metrics_mod
from core.utils.health import (
    build_liveness_payload,
    build_readiness_payload,
    get_health_paths,
    health_state,
)

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict):
        self.config = config
        self.logger = setup_logging()
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)
        metrics_mod.init_metrics(config)
        health_state.bind(config=config)

    def apply_config(self, config: dict) -> None:
        """与 WebSocketServer.update_config 对齐：同步 HTTP/OTA/Vision/health 侧配置。"""
        self.config = config or {}
        self.ota_handler.apply_config(self.config)
        if hasattr(self.vision_handler, "apply_config"):
            self.vision_handler.apply_config(self.config)
        else:
            self.vision_handler.config = self.config
        metrics_mod.init_metrics(self.config)
        health_state.bind(config=self.config)
        self.logger.bind(tag=TAG).info(
            "HTTP/OTA/health 配置已与热更新同步 "
            f"(auth={self.ota_handler.auth_enable}, "
            f"whitelist_bypass={self.ota_handler.whitelist_bypass}, "
            f"allowlist_only={self.ota_handler.devices_allowlist_only})"
        )

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        """获取websocket地址

        Args:
            local_ip: 本地IP地址
            port: 端口号

        Returns:
            str: websocket地址
        """
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket")

        if websocket_config and "你" not in websocket_config:
            return websocket_config
        else:
            return f"ws://{local_ip}:{port}/xiaozhi/v1/"

    async def handle_metrics(self, request):
        """Prometheus metrics exposition"""
        body = metrics_mod.render_latest()
        return web.Response(
            body=body, headers={"Content-Type": metrics_mod.content_type()}
        )

    async def handle_liveness(self, request):
        """进程存活（liveness）。"""
        return web.json_response(build_liveness_payload(self.config), status=200)

    async def handle_readiness(self, request):
        """能否接新流量（readiness）。"""
        ready, payload = await build_readiness_payload(self.config)
        return web.json_response(payload, status=200 if ready else 503)

    async def start(self):
        try:
            server_config = self.config["server"]
            read_config_from_api = self.config.get("read_config_from_api", False)
            host = server_config.get("ip", "0.0.0.0")
            port = int(server_config.get("http_port", 8003))
            metrics_cfg = server_config.get("metrics") or {}
            metrics_path = metrics_cfg.get("path", "/metrics")
            if not str(metrics_path).startswith("/"):
                metrics_path = "/" + str(metrics_path)
            health_cfg = server_config.get("health") or {}
            health_enabled = bool(health_cfg.get("enabled", True))
            live_path, ready_path = get_health_paths(self.config)

            if port:
                app = web.Application()

                if not read_config_from_api:
                    # 如果没有开启智控台，只是单模块运行，就需要再添加简单OTA接口，用于下发websocket接口
                    app.add_routes(
                        [
                            web.get("/xiaozhi/ota/", self.ota_handler.handle_get),
                            web.post("/xiaozhi/ota/", self.ota_handler.handle_post),
                            web.options(
                                "/xiaozhi/ota/", self.ota_handler.handle_options
                            ),
                            # 下载接口，仅提供 data/bin/*.bin 下载
                            web.get(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_download,
                            ),
                            web.options(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_options,
                            ),
                        ]
                    )
                # 添加路由
                routes = [
                    web.get("/mcp/vision/explain", self.vision_handler.handle_get),
                    web.post("/mcp/vision/explain", self.vision_handler.handle_post),
                    web.options(
                        "/mcp/vision/explain", self.vision_handler.handle_options
                    ),
                ]
                if health_enabled:
                    routes.append(web.get(live_path, self.handle_liveness))
                    routes.append(web.get(ready_path, self.handle_readiness))
                if metrics_cfg.get("enabled", True):
                    routes.append(web.get(metrics_path, self.handle_metrics))
                app.add_routes(routes)

                # 运行服务
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()
                health_state.mark_http_started()
                if health_enabled:
                    self.logger.bind(tag=TAG).info(
                        f"Health checks: http://{host}:{port}{live_path} "
                        f"(liveness), http://{host}:{port}{ready_path} (readiness)"
                    )
                if metrics_cfg.get("enabled", True):
                    self.logger.bind(tag=TAG).info(
                        f"Prometheus metrics: http://{host}:{port}{metrics_path}"
                    )

                # 保持服务运行
                while True:
                    await asyncio.sleep(3600)  # 每隔 1 小时检查一次
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP服务器启动失败: {e}")
            import traceback

            self.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
            raise
