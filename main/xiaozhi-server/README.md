# xiaozhi-server 生产级改造说明

本文档记录将 `xiaozhi-server`（实时语音 WebSocket 服务）提升为企业生产级时的**问题诊断、改造优先级，以及已落地的改动**。

> 范围：`main/xiaozhi-server`  
> 性质：有状态长连接网关（每设备一条 WebSocket，挂载 VAD / ASR / LLM / TTS、音频缓冲、线程池、上报队列）

---

## 1. 核心结论

企业生产级改造，**最重要的不是先把代码写漂亮或先补全测试**，而是：

> **把「每设备一条有状态长连接」做成：有上限、可回收、可观测、失败可隔离。**

线上最怕的是：连接断了资源没清干净 → 内存 / FD / 线程泄漏 → 整机拖垮。

---

## 2. 问题清单（按业务风险）

| 优先级 | 问题 | 风险 |
|--------|------|------|
| P0 | 无连接硬上限 | 突发连接打满进程，OOM / 事件循环卡死 |
| P0 | 清理不完整、不幂等 | 多路径同时 `close`，任务/线程/队列泄漏 |
| P1 | 可观测性不足 | 有日志，缺连接水位、ASR/TTS/LLM 耗时与失败率、队列积压 |
| P1 | 安全基线弱 | auth 可关、白名单免 token、token 可走 query、密钥派生固定 salt、部分硬编码 key |
| P2 | 上游依赖缺韧性 | ASR/TTS/LLM/manager-api 缺统一超时、重试、熔断、降级 |
| P3 | `ConnectionHandler` 过大 | ~1800 行 god object，难测、难演进 |
| P3 | 几乎无自动化测试 | 回归靠手工，生产改动风险高 |

---

## 3. 改造路线图

建议顺序（已完成项打勾）：

1. [x] **硬上限**：全局并发、同设备并发、上报队列有界
2. [x] **确定性清理**：幂等 `close()`、任务登记与统一取消、固定清理顺序、清理超时
3. [x] **可观测性**：连接数 / 拒绝数、会话生命周期、ASR/TTS/LLM 延迟与错误率、队列深度（Prometheus）
4. [ ] **安全基线**：默认开启认证、禁止 query 传 token、密钥与盐值规范化、去掉硬编码密钥
5. [ ] **依赖韧性**：统一超时、有限重试、熔断、对设备侧友好降级话术
6. [ ] **结构拆分与测试**：拆分 `ConnectionHandler`、补连接生命周期与限流单测

---

## 4. 已落地：硬上限

### 4.1 做了什么

新增连接登记表，在创建业务 Handler **之前**做准入：

- 文件：`core/connection_registry.py`
- 接入：`core/websocket_server.py`
- 超限关闭码：`1013`（Try Again Later）
- HTTP 探活返回当前连接水位（非完整 readiness，仅水位提示）

### 4.2 配置项

写在 `config.yaml` 的 `server.connection`：

```yaml
server:
  connection:
    # 单进程最大并发 WebSocket 连接数
    max_connections: 500
    # 同一 device-id 最大并发连接数（ESP 设备通常 1～2）
    max_connections_per_device: 2
    # 聊天上报队列上限，满则丢弃新上报，避免内存膨胀
    report_queue_maxsize: 100
    # 连接清理等待超时（秒）
    cleanup_timeout_seconds: 10
```

未配置时使用上述默认值（见 `ConnectionLimits.from_config`）。

若使用智控台 / `manager-api` 下发配置，请在对应服务端配置中同步这些字段，否则可能仍走默认值。

### 4.3 行为说明

| 场景 | 行为 |
|------|------|
| 全局连接数 ≥ `max_connections` | 拒绝新连接，日志 warning，WS close `1013` |
| 同 `device-id` 连接数 ≥ `max_connections_per_device` | 同上 |
| 连接正常结束 / 异常结束 | `finally` 中 `release`，名额幂等回收 |
| 访问 WS 端口的普通 HTTP | 返回 `active_connections` / `max_connections` |

按机器规格调整上限：CPU / 内存越高可适当加大 `max_connections`；单设备一般保持 `1` 或 `2`。

---

## 5. 已落地：确定性清理

### 5.1 「确定性」是什么意思

设备断开后，资源释放不再靠「大概关一下」，而是：

1. **固定顺序**清理  
2. **尽量不漏项**  
3. **关一次即可**（幂等）  
4. **某步卡住有超时**，不把整个进程卡死在 `close()` 里  

### 5.2 「幂等」是什么意思

同一个 `close()` 调用一次和多次，最终效果一样：

- 第一次：真正释放资源  
- 之后再调：直接返回，不重复清理、不刷无关错误  

原因：断开时可能多条路径同时想关（客户端断开、`finally`、空闲超时、服务端强制关）。没有幂等容易竞态或重复操作已释放对象。

实现标志：`_closing` / `_closed`。

### 5.3 改了哪些点（逐条）

#### （1）会话状态与任务名单

位置：`ConnectionHandler.__init__`

| 变量 | 作用 |
|------|------|
| `_closing` | 正在关闭 |
| `_closed` | 已关闭完成 |
| `_tracked_tasks` | 本会话创建的 asyncio 任务集合 |
| `_cleanup_timeout_seconds` | 清理步骤超时 |

#### （2）上报队列有界

- 以前：`queue.Queue()` 无限增长  
- 现在：`maxsize=report_queue_maxsize`，满则丢弃（见 `reportHandle._enqueue_report`）

#### （3）`spawn_task`：开任务并记账

- 以前：`asyncio.create_task(...)` 开完就不管  
- 现在：经 `spawn_task` 登记到 `_tracked_tasks`  
- 连接已在关闭流程中时，拒绝再开新任务  

已改用 `spawn_task` 的包括：超时检查、AEC 缓存清理、后台初始化、绑定提示、VAD resume 等。

#### （4）`_cancel_tracked_tasks`

关闭时按名单 `cancel` 所有未完成任务，并 `wait_for` 等待（带超时）。  
不会取消「当前正在执行 close 的任务本身」，避免自取消导致清理做到一半中断。

#### （5）`_drain_queue` / `_stop_report_thread` / `_safe_close_websocket`

| 方法 | 作用 |
|------|------|
| `_drain_queue` | 非阻塞掏空队列残留 |
| `_stop_report_thread` | 投递毒丸 `None` + `join`，正式停上报线程 |
| `_safe_close_websocket` | 已关闭则跳过，失败不拖垮整体清理 |

#### （6）重写后的 `close()` 固定顺序

| 步骤 | 动作 |
|------|------|
| 守卫 | 已关闭 / 正在关闭 → 直接 return |
| 1 | `stop_event.set()`，打断后续业务 |
| 2 | 取消会话级后台任务 |
| 3 | 释放 VAD 连接资源 |
| 4 | 清理 opus 解码器与音频缓冲 / ASR 队列 |
| 5 | 清理 AEC 缓存 |
| 6 | 工具处理器 `cleanup`（带超时） |
| 7 | 清空业务队列 + 停止上报线程 |
| 8 | 关闭 WebSocket |
| 9 | 关闭 TTS / ASR 上游（带超时） |
| 10 | `executor.shutdown` |
| finally | `_closed = True` |

#### （7）空闲超时不再直接 `close()`

- 以前：超时任务里 `await self.close()`，容易和「取消自身」打架  
- 现在：超时只 `stop_event.set()` + 关 WebSocket  
- 真正清理统一走 `handle_connection` 的 `finally` → `_save_and_close` → `close()`  

---

## 6. 关键文件一览

| 文件 | 变更 |
|------|------|
| `core/connection_registry.py` | **新建**：连接硬上限与会话登记 |
| `core/websocket_server.py` | 准入校验、登记/释放、探活水位、配置热更新同步 limits |
| `core/connection.py` | 幂等 `close()`、任务跟踪、有界上报队列、确定性清理 |
| `core/handle/reportHandle.py` | `put_nowait` + 队列满丢弃 |
| `core/handle/receiveAudioHandle.py` | VAD resume 走 `spawn_task` |
| `config.yaml` / `data/.config.yaml` | `server.connection`、`server.metrics` |
| `core/utils/metrics.py` | **新建**：Prometheus 指标封装 |
| `core/http_server.py` | 暴露 `/metrics` |
| `core/providers/asr/base.py` / `tts/base.py` | ASR/TTS 延迟与错误率 |
| `scripts/ws_lifecycle_smoke.py` | A/B/C 连接生命周期冒烟 |

---

## 6.1 已落地：可观测性（Prometheus）

### 指标一览

| 指标 | 类型 | 含义 |
|------|------|------|
| `xiaozhi_ws_active_connections` | Gauge | 当前活跃 WS 会话 |
| `xiaozhi_ws_max_connections` | Gauge | 配置的全局上限 |
| `xiaozhi_ws_rejected_total{reason}` | Counter | 硬上限拒绝（capacity / device_limit） |
| `xiaozhi_ws_sessions_opened_total` | Counter | 成功准入会话数 |
| `xiaozhi_ws_session_duration_seconds` | Histogram | 会话存活时长 |
| `xiaozhi_provider_requests_total{component,provider,status}` | Counter | ASR/TTS/LLM 请求（ok/error/empty） |
| `xiaozhi_provider_latency_seconds{component,provider}` | Histogram | 端到端耗时 |
| `xiaozhi_provider_ttfb_seconds{component,provider}` | Histogram | LLM 首 token 时延 |
| `xiaozhi_queue_depth{queue}` | Gauge | `report` / `tts_text` / `tts_audio` 队列深度 |

### 配置

写在 `data/.config.yaml`（本地覆盖）或 `config.yaml`：

```yaml
server:
  metrics:
    enabled: true
    path: /metrics
```

拉取地址：`http://<host>:<http_port>/metrics`（默认端口 `8003`）

依赖：`prometheus_client`（见 `requirements.txt`）

---

## 7. 如何验证

1. **启动日志**应出现类似：  
   `连接硬上限: max=500, per_device=2, report_queue=100`  
   `Prometheus metrics: http://0.0.0.0:8003/metrics`
2. **HTTP 访问 WS 端口**（非 Upgrade）应看到：  
   `active_connections=...` / `max_connections=...`
3. **压测同 device-id** 超过 `max_connections_per_device`：新连接被拒，日志含 `连接被拒绝`
4. **正常断开 / 超时断开**：日志出现 `连接资源已释放 session=... device=...`，连接水位回落
5. **重复触发关闭**（客户端断 + 服务端 finally）：不应出现成片二次清理异常
6. **curl 指标**：`curl http://127.0.0.1:8003/metrics | findstr xiaozhi`

---

## 8. 后续建议（未做）

1. **真 readiness**：区分 liveness / readiness（依赖、队列、连接水位）
2. **安全**：生产默认 `auth.enabled=true`；token 仅 Header；清理硬编码密钥
3. **熔断降级**：上游超时后对设备播放固定提示音/短句，避免静默挂起
4. **单测**：至少覆盖 `ConnectionRegistry` 限流与 `close()` 幂等路径
5. **OTel traces**（第二期）：会话级链路追踪，指标仍可导出到 Prometheus

---

## 9. 相关入口

- 主程序：`app.py`
- WebSocket 服务：`core/websocket_server.py`
- 单连接会话：`core/connection.py`
- 默认配置：`config.yaml`
- 从智控台拉配置：`config_from_api.yaml`（需自备 `data/.config.yaml`）

部署与功能集成文档见仓库 `docs/` 目录（如 [Deployment.md](../../docs/Deployment.md)）。
