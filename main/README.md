# xiaozhi-esp32-server 生产级改造说明

本仓库业务能力继承自开源小智后端。感谢**华南理工大学刘思源教授团队**的主导研发，以及 [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server) 维护者与全体贡献者。功能基线已跑通语音与智控台链路；同时，有状态长连接网关在上限、清理、可观测、安全与韧性等方面距生产上线仍有差距。本分支据此做了一系列加固。

细则见 [`xiaozhi-server/README.md`](./xiaozhi-server/README.md)；运维上线见 [`docs/Production.md`](../docs/Production.md)；仓库总览见根目录 [`README.md`](../README.md)。

---

## 1. 基线与目标

| 节点 | Commit | 含义 |
|------|--------|------|
| **功能基线** | [`de45f73efdd24e9343427a56b5d22f857b6bb7a7`](https://github.com/xinnan-tech/xiaozhi-esp32-server/commit/de45f73efdd24e9343427a56b5d22f857b6bb7a7) | 上游已具备端到端语音交互、智控台、插件与多组件架构；**功能可用，生产就绪度不足** |
| **当前目标态** | [`7519dd516c79764eb722fe3c25239d6f30f665c8`](https://github.com/xinnan-tech/xiaozhi-esp32-server/commit/7519dd516c79764eb722fe3c25239d6f30f665c8) | 在基线上完成连接硬上限、确定性清理、Prometheus、环境安全策略、上游韧性、Dialogue 注册心跳、探活与发布门禁等改造 |

> **一句话：** 从「能演示、能联调」推进到「有上限、可回收、可观测、失败可隔离」。

---

## 2. 为什么要改：基线离生产还差什么

`xiaozhi-server` 是有状态长连接网关：每台 ESP32 一条 WebSocket，挂载 VAD / ASR / LLM / TTS、音频缓冲、线程池与上报队列。基线把业务链路实现了，但线上最怕的是——**连接断了资源没清干净 → 内存 / FD / 线程泄漏 → 整机拖垮**。

按业务风险梳理（详见 [`xiaozhi-server/README.md`](./xiaozhi-server/README.md)）：

| 优先级 | 问题 | 风险 |
|--------|------|------|
| P0 | 无连接硬上限 | 突发连接打满进程，OOM / 事件循环卡死 |
| P0 | 清理不完整、不幂等 | 多路径同时 `close`，任务/线程/队列泄漏 |
| P1 | 可观测性不足 | 有日志，缺连接水位、ASR/TTS/LLM 耗时与失败率、队列积压 |
| P1 | 安全基线弱 | auth 可关、白名单免 token、token 可走 query、硬编码 key 兜底 |
| P2 | 上游依赖缺韧性 | ASR/TTS/LLM 超时/失败时缺少统一重试、熔断与设备侧降级 |
| P3 | `ConnectionHandler` 过大、几乎无自动化测试 | 难演进、回归靠手工 |

企业生产级改造**最重要的不是先把代码写漂亮或先补全测试**，而是先把「每设备一条有状态长连接」做成：**有上限、可回收、可观测、失败可隔离**。

---

## 3. 改造路线图（基线 → 当前）

建议顺序（相对功能基线，目标态已完成项打勾）：

1. [x] **硬上限**：全局并发、同设备并发、上报队列有界
2. [x] **确定性清理**：幂等 `close()`、任务登记与统一取消、固定清理顺序、清理超时
3. [x] **可观测性**：连接数 / 拒绝数、会话生命周期、ASR/TTS/LLM 延迟与错误率、队列深度（Prometheus `/metrics`）
4. [x] **安全基线（按环境）**：`development` 保留联调兼容；`production` 禁止 query token、禁止硬编码 key 兜底、默认强制 auth、默认禁止白名单免检
5. [x] **依赖韧性**：统一失败语义、有限重试、熔断、对设备侧友好降级话术 / 预置音
6. [x] **Dialogue 注册心跳**：多实例向 Redis 注册，OTA 优先选存活实例
7. [x] **探活与发布门禁**：`/health` + `/ready`；核心策略单测与 CI（`.github/workflows/xiaozhi-server-tests.yml`）

改造重心在 **`main/xiaozhi-server`**（实时语音 WebSocket 服务）；`manager-api` 侧配合参数下发、Dialogue 选路与 Liquibase 变更。微服务拆分（`xiaozhi-microserver/`）是后续演进方向，与单体生产加固可并行，但不替代上述 P0/P1 能力。

---

## 4. 已落地能力摘要

### 4.1 连接硬上限与确定性清理

- 在业务 Handler **之前**准入：全局 `max_connections`、同 `device-id` 上限；超限关码 `1013`
- 上报队列有界；会话任务经 `spawn_task` 登记，关闭时统一取消
- 幂等 `close()`：固定清理顺序 + 单步超时，避免多路径竞态泄漏

### 4.2 可观测与探活

- Prometheus：活跃连接、拒绝原因、会话时长、provider 延迟/错误率、队列深度、熔断状态、降级与过载丢弃等
- `GET /health`（liveness）、`GET /ready`（readiness：连接打满 / registry Redis 失败返回 503）

### 4.3 环境安全与依赖韧性

- `server.environment` / `XIAOZHI_ENV`：development 便于联调，production 收紧 auth、token 传递与白名单策略
- ASR/LLM/TTS 超时、重试、熔断；失败时设备侧降级话术或预置音；可选 LLM fallback 与过载背压

### 4.4 多实例发现

- 各 `xiaozhi-server` 进程向与 manager-api **同库** Redis 注册 WebSocket 地址并心跳；OTA 优先从存活实例选路

配置项、指标表、验证步骤与文件变更一览，以 [`xiaozhi-server/README.md`](./xiaozhi-server/README.md) 为准。

---

## 5. 整体架构（组件职责未变）

系统仍是「硬件 ↔ 实时语音网关 ↔ 智控台」的多组件协作；生产改造**不改变产品形态**，而是把网关与运维面补齐。

```
xiaozhi-esp32-server
  ├─ xiaozhi-server     :8000  Python   与 ESP32 的 WebSocket 语音链路（改造主战场）
  ├─ manager-web        :8001  Vue      智控台 Web
  ├─ manager-api        :8002  Java     智控台 API / 配置 / OTA 选路
  ├─ manager-mobile            uni-app  移动版智控台
  ├─ digital-human             Python   数字人联调（页面 / 唤醒词 / 事件桥）
  └─ xiaozhi-microserver       Python   微服务拆分演进（可选）
```

| 组件 | 职责 |
|------|------|
| **ESP32 设备** | 采音、播报、执行 IoT/MCP 指令 |
| **`xiaozhi-server`** | VAD → ASR → LLM → TTS；插件与工具调用；从 manager-api 拉配置 |
| **`manager-api`** | 用户/设备/模型配置持久化（MySQL）；Redis 缓存与 Dialogue 注册选路；OTA |
| **`manager-web` / `manager-mobile`** | 图形化管理与移动端运维入口 |
| **`digital-human`** | 独立联调数字人页面与本地唤醒词 |

**两条主线：**

- **语音交互：** ESP32 ↔ WebSocket ↔ `xiaozhi-server`（实时、有状态）
- **管理配置：** 浏览器/App ↔ HTTP ↔ `manager-api`；`xiaozhi-server` 启动或热更新时拉取配置

协议说明见 [小智通信协议](https://ccnphfhqs21z.feishu.cn/wiki/M0XiwldO9iJwHikpXD5cEx71nKh)。部署方式见 [Deployment.md](../docs/Deployment.md) / [Deployment_all.md](../docs/Deployment_all.md)。

---

## 6. 如何验证改造是否生效

1. 启动日志含：运行环境、连接硬上限、`Prometheus metrics` 地址；多实例时含 `Dialogue 已注册到 Redis`
2. `curl http://127.0.0.1:8003/health`、`/ready`、`/metrics`（指标名含 `xiaozhi_`）
3. 同 `device-id` 超并发：新连接被拒（`1013` / 日志「连接被拒绝」）；断开后水位回落
4. 人为让 ASR/LLM 失败：设备听到降级话术或预置音，speaking 能正常 stop
5. 本地门禁：`cd main/xiaozhi-server && python scripts/production_verify.py`

生产开箱与必做清单见 [`docs/Production.md`](../docs/Production.md)。

---

## 7. 相关入口

| 路径 | 说明 |
|------|------|
| [`xiaozhi-server/README.md`](./xiaozhi-server/README.md) | 生产改造详细诊断、配置、指标与验证 |
| [`docs/Production.md`](../docs/Production.md) | 生产部署、探活、容量 |
| [`docs/Deployment.md`](../docs/Deployment.md) | 仅 Server 部署 |
| [`docs/Deployment_all.md`](../docs/Deployment_all.md) | 全模块部署 |
| `xiaozhi-server/app.py` | 对话网关入口 |
| `xiaozhi-server/config.yaml` | 默认配置（主线多为 development；生产用 compose overlay） |

后续建议：单体生产加固（上限 / 清理 / 可观测 / 安全 / 韧性）已完成后，优先按**微服务拆分**推进；可选补齐连接准入全路径单测、adapter 统一 `UpstreamError`、注册加权选路等，与拆分并行即可。
