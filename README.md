# xiaozhi-esp32-server · 生产加固分支（微服务）

> **先选架构，再部署。** 本仓库在生产加固基线之上提供两套运行时：当前 **`Microservices_architecture` 即为六微服务架构**；另有单体分支 [`main`](https://github.com/Jehuty-ML/prd_xiaozhi_server/tree/main)。请按实际规模与运维能力选用，**不必默认上微服务**。

## 架构选用（必读）

| 分支 | 运行时 | 适合谁 | 代价 / 收益 |
|------|--------|--------|-------------|
| [`main`](https://github.com/Jehuty-ML/prd_xiaozhi_server/tree/main)（单体） | `main/xiaozhi-server` | 业务刚起步、设备量不大、希望少人维护 | **部署与排障成本低**；单进程即可跑通全链路。需要扩容时，也可多实例 + 负载均衡，并配合智控台 / Redis 注册做一定程度的横向扩展 |
| **`Microservices_architecture`（当前分支 · 微服务）** | 六微服务 `main/xiaozhi-microserver` | 高并发接入、要按模块弹性扩容、接入层与 ASR/LLM/TTS 需隔离 | **运维与联调成本更高**（多进程、发现、跨服务契约）；换来的是按瓶颈单独扩 access / receiver / agent / speaker 等，以及更好的故障隔离 |

**怎么选：**

1. 多数团队起步应优先留在 **`main`（单体）**——能更快交付，也够支撑相当一段时间的并发。
2. 明确遇到「单机连接/推理互相拖垮」或「必须按语音链路分段扩容」时，再使用本分支 **`Microservices_architecture`**。

```bash
# 切回单体（低运维成本）时
git checkout main
```

更细的取舍说明见本分支 [`main/xiaozhi-microserver/README.md`](./main/xiaozhi-microserver/README.md)。

---

## 致谢与定位

本仓库的业务能力与工程基础，来自开源项目 [xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) 及小智生态各方贡献者。

特别感谢：

- **华南理工大学刘思源教授团队**对小智后端服务的主导研发与持续投入  
- 上游仓库 [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) 的维护者与全体 [代码贡献者](https://github.com/xinnan-tech/xiaozhi-esp32-server/graphs/contributors)  
- 固件侧 [xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)、[小智通信协议](https://ccnphfhqs21z.feishu.cn/wiki/M0XiwldO9iJwHikpXD5cEx71nKh) 及相关生态项目  

在功能基线（Commit [`de45f73`](https://github.com/xinnan-tech/xiaozhi-esp32-server/commit/de45f73efdd24e9343427a56b5d22f857b6bb7a7)）上，端到端语音交互、智控台、插件与多 Provider 架构已经可用。与此同时，作为有状态长连接网关，基线距可上线的生产环境仍有明显差距，例如：

- 缺少连接硬上限，突发流量易打满进程  
- 断线清理不完整、不幂等，存在任务 / 线程 / 队列泄漏风险  
- 可观测性偏日志，缺少连接水位与 ASR/TTS/LLM 延迟、失败率等指标  
- 安全默认偏联调（auth 可关、白名单免检、query 传 token 等）  
- 上游依赖缺少统一超时、重试、熔断与设备侧降级  

本分支在上述基线之上做了一系列生产加固，并将语音网关拆为 **Nacos + gRPC 六微服务**。技术栈仍是 Python 语音链路 + Java 智控台 + Vue 前端；**补齐的是连接治理、可观测、安全基线、依赖韧性，以及按模块扩容的能力**。

社区发行说明、演示视频与 Provider 全家桶仍以[上游 README](https://github.com/xinnan-tech/xiaozhi-esp32-server) 为准；本文只描述本分支的差异与上线用法。

| 文档 | 用途 |
|------|------|
| [生产部署](./docs/Production.md) | 上线必做、探活、容量 |
| [技术说明 `main/`](./main/README.md) | 架构与组件拆解 |
| [微服务 README](./main/xiaozhi-microserver/README.md) | 六服务职责、本地启动、配置 |
| [六服务部署](./docs/Deployment.md) / [全模块部署](./docs/Deployment_all.md) | 安装步骤 |
| [FAQ](./docs/FAQ.md) | 常见问题 |

---

## 与功能基线的差异

| 维度 | 功能基线（`de45f73`） | 本分支（微服务 + 生产加固） |
|------|----------------------|---------------------------|
| 定位 | 功能完整、便于演示与自建 | 在功能之上补自托管生产面，并拆分语音链路 |
| 运行时 | 单体 `xiaozhi-server` | 六微服务 `xiaozhi-microserver`（本分支不保留单体目录） |
| 连接 | 基本无限接纳 | 全局 / 单设备硬上限，超限关闭码 `1013` |
| 断线清理 | 路径不统一 | 幂等清理、peer Abort、重连不误杀新会话 |
| 可观测 | 以日志为主 | access / admin 的 Prometheus `/metrics`，以及 `/health`、`/ready` |
| 安全 | 默认偏联调友好 | `production` 强制 auth、禁止 query token、控制面 token 门禁 |
| 上游故障 | 易直接暴露给设备 | 超时 / 重试 / 熔断 / 降级话术 |
| 扩容 | 单进程纵向加压 | 按 access / receiver / agent / speaker 等分段扩容 |

改造摘要：连接准入与确定性回收；会话状态机与分布式门禁；按环境区分的安全策略；ASR/LLM/TTS 韧性；配置热更与冒烟 / 单测 CI。细则见 [`main/README.md`](./main/README.md)、[`main/xiaozhi-microserver/README.md`](./main/xiaozhi-microserver/README.md)。

```
main/
  xiaozhi-microserver/ 六微服务语音主线（本分支主运行时）
    access :8000 / gRPC    设备 WS、鉴权、会话、下行
    agent  gRPC            LLM / 意图 / 工具 / 记忆
    speaker gRPC           TTS、播报队列、AEC 参考音
    preprocess gRPC        VAD / AEC / 听状态
    receiver gRPC          ASR
    control-admin :8003    OTA / Vision / 配置 / metrics
  manager-api/         智控台 API :8002
  manager-web/         智控台 Web :8001
  manager-mobile/      移动智控台
  digital-human/       数字人联调
```

设备侧常用地址：WebSocket `ws://host:8000/xiaozhi/v1/`，OTA `http://host:8003/xiaozhi/ota/`。

语音交互、多 Provider、插件 / MCP / IoT、智控台、OTA、声纹与知识库等业务能力均予保留。

---

## 开箱部署

默认配置为 **development**，请勿直接对公网暴露。生产环境使用 compose 叠加层：

```bash
cd main/xiaozhi-microserver

# 开发：源码一键起六服务（需本机 Nacos 等，见微服务 README）
# Windows: start_dev_services.bat
# Linux/macOS: bash start_dev_services.sh

# Docker 六服务
docker compose -f docker-compose.yml up -d

# 生产叠加
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# 全模块（智控台 + MySQL + Redis + 六服务）
docker compose -f docker-compose_all.yml up -d
```

部署后将 websocket / OTA / vision 等地址改为设备可达地址；对接智控台时配置 `manager-api` 的 url / secret。完整清单见 [Production.md](./docs/Production.md)。

| 方式 | 文档 | 说明 |
|------|------|------|
| 六微服务 | [Deployment.md](./docs/Deployment.md) | 本分支默认语音主线 |
| 全模块 | [Deployment_all.md](./docs/Deployment_all.md) | MySQL + Redis + 智控台 |
| 生产 | [Production.md](./docs/Production.md) | 环境变量、探活、容量 |

安装步骤可沿用上游文档结构；**生产环境开关与探活以本仓库 Production 文档为准**。

---

## 上线自检

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/ready
curl -sS http://127.0.0.1:8003/health
curl -sS http://127.0.0.1:8003/ready
curl -sS http://127.0.0.1:8000/metrics | findstr xiaozhi   # Windows；Linux 使用 grep
curl -sS http://127.0.0.1:8003/metrics | findstr xiaozhi

cd main/xiaozhi-microserver
python scripts/run_tests.py --unit-only
# 服务已起来后可再跑 scripts/*_smoke.py
```

期望结果：生产环境下认证开启、无 token / 错误密钥被拒绝；`/metrics` 含连接水位等相关指标。

---

## 警告

1. 本软件与任何第三方 ASR / LLM / TTS 等服务商无商业合作关系，不为其服务质量或资金安全提供担保；密钥由使用者自行保管。  
2. 未按 [Production.md](./docs/Production.md) 完成环境与密钥收紧前，请勿对公网开放。

---

## 许可与链接

- 许可证：与上游一致（MIT），见 [LICENSE](./LICENSE)  
- 上游项目：[xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)  
- 硬件固件：[78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)  
- 单体加固分支：[main](https://github.com/Jehuty-ML/prd_xiaozhi_server/tree/main)

微服务细则：[`main/xiaozhi-microserver/README.md`](./main/xiaozhi-microserver/README.md)。通用安装问题：[FAQ](./docs/FAQ.md)。
