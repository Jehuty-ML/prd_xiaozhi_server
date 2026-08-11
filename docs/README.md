# 文档索引（`Microservices_architecture`）

本目录只保留**本分支仍适用**的文档。已删除的上游单体插件 / TTS / MCP 接入点等说明，请到 [`main` 单体分支](https://github.com/Jehuty-ML/prd_xiaozhi_server/tree/main) 或[上游仓库](https://github.com/xinnan-tech/xiaozhi-esp32-server)查阅。

## 部署与生产

| 文档 | 说明 |
|------|------|
| [../README.md](../README.md) | 架构选用、生产加固差异、开箱与自检 |
| [Production.md](./Production.md) | 生产必做、探活、容量 |
| [Deployment.md](./Deployment.md) | 六微服务 Docker / 源码 |
| [Deployment_all.md](./Deployment_all.md) | 六服务 + 智控台 |
| [docker-build.md](./docker-build.md) | 自建 microserver 镜像 |
| [dev-ops-integration.md](./dev-ops-integration.md) | 源码自动更新（六服务，无 `app.py`） |
| [FAQ.md](./FAQ.md) | 常见问题 |
| [../main/xiaozhi-microserver/README.md](../main/xiaozhi-microserver/README.md) | 六服务职责与本地启动 |

## 设备 / 固件 / OTA

| 文档 | 说明 |
|------|------|
| [ota-upgrade-guide.md](./ota-upgrade-guide.md) | control-admin OTA 与 `bin/` |
| [firmware-setting.md](./firmware-setting.md) | 成品固件改 OTA 地址 |
| [firmware-build.md](./firmware-build.md) | 自行编译固件 |

## 本分支仍有代码对应的集成

| 文档 | 对应实现 |
|------|----------|
| [weather-integration.md](./weather-integration.md) | agent 插件 `get_weather`（Open-Meteo） |
| [mcp-vision-integration.md](./mcp-vision-integration.md) | control-admin `/mcp/vision/explain`（推理仍为 stub） |
| [mqtt-gateway-integration.md](./mqtt-gateway-integration.md) | 外置 MQTT 网关 → access WS；OTA 可下发 mqtt |
| [ali-sms-integration.md](./ali-sms-integration.md) | manager-api 阿里云短信注册 |
| [digital-human-wakeword.md](./digital-human-wakeword.md) | `main/digital-human` |
| [all-in-one-digital-human-setup.md](./all-in-one-digital-human-setup.md) | 同上，整机部署 |

## 已从本分支 docs 删除（无 microserver 对应能力）

Home Assistant、联网搜索、NewsNow、设备呼叫、声纹、RAGFlow、PowerMem、上下文源、MCP 接入点、Fish/Paddle/Index/火山克隆 TTS、性能压测脚本等文档已移除，避免按过时步骤操作。
