# xiaozhi-server 生产级改造说明

> ⚠️ **第六期已收口**：本目录已退役为主运行时。请改用
> [`../xiaozhi-microserver`](../xiaozhi-microserver)。详见 [RETIRED.md](./RETIRED.md)。

本文档记录将 `xiaozhi-server`（实时语音 WebSocket 服务）提升为企业生产级时的**问题诊断、改造优先级，以及已落地的改动**。

> 范围：`main/xiaozhi-server`（历史单体，仅供对照）  
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
| P2 | 上游依赖缺韧性（已落地基础版） | ASR/TTS/LLM/manager-api：统一失败语义 + 超时/重试/熔断 + 设备侧降级话术 |
| P3 | `ConnectionHandler` 过大 | ~1800 行 god object，难测、难演进 |
| P3 | 几乎无自动化测试 | 回归靠手工，生产改动风险高 |

---

## 3. 改造路线图

建议顺序（已完成项打勾）：

1. [x] **硬上限**：全局并发、同设备并发、上报队列有界
2. [x] **确定性清理**：幂等 `close()`、任务登记与统一取消、固定清理顺序、清理超时
3. [x] **可观测性**：连接数 / 拒绝数、会话生命周期、ASR/TTS/LLM 延迟与错误率、队列深度（Prometheus）
4. [x] **安全基线（按环境）**：`development` 保留联调兼容；`production` 禁止 query token、禁止硬编码 key 兜底、默认强制 auth、AuthToken 盐值规范化、**默认禁止白名单免检**
5. [x] **依赖韧性**：统一失败语义、有限重试、熔断、对设备侧友好降级话术
6. [x] **发布门禁单测**：session / resilience / registry / health / runtime_env；CI 见 `.github/workflows/xiaozhi-server-tests.yml`（连接准入全路径单测仍可选）

---

## 4. 已落地：硬上限

### 4.1 做了什么

新增连接登记表，在创建业务 Handler **之前**做准入：

- 文件：`core/connection_registry.py`
- 接入：`core/websocket_server.py`
- 超限关闭码：`1013`（Try Again Later）
- HTTP 探活返回当前连接水位（非完整 readiness，仅水位提示）

### 4.2 配置项

#### 本地模式（无智控台）

写在 `data/.config.yaml` 的 `server.connection`。

#### 智控台模式（manager-api）

参数写入 `sys_params`，在【参数管理】可改：

| param_code | 默认 | 说明 |
|------------|------|------|
| `server.connection.max_connections` | 200 | 单进程最大 WS 连接数 |
| `server.connection.max_connections_per_device` | 2 | 同 device-id 上限 |
| `server.connection.report_queue_maxsize` | 100 | 上报队列上限 |
| `server.connection.cleanup_timeout_seconds` | 10 | 清理超时（秒） |
| `server.metrics.enabled` | true | 是否启用 Prometheus `/metrics` |
| `server.metrics.path` | /metrics | 指标路径（挂在 http_port） |
| `server.resilience.enabled` | true | 是否启用上游韧性（超时/重试/熔断/降级） |
| `server.resilience.max_retries` | 2 | 瞬时故障最大重试次数 |
| `server.resilience.asr_timeout_seconds` | 15 | ASR 单次超时（秒） |
| `server.resilience.tts_max_retries` | 3 | TTS 合成最大重试 |
| `server.resilience.circuit_failure_threshold` | 5 | 熔断连续失败阈值 |
| `server.resilience.circuit_open_seconds` | 30 | 熔断开路冷却（秒） |
| `server.resilience.asr` / `llm` / `tts` / `tool` | （见 config.yaml） | 各阶段降级话术 |
| `server.resilience.llm_fallback` | （空） | 备用 LLM 配置名（`LLM` 段键） |
| `server.resilience.tts_fallback_audio` | `config/assets/wakeup_words_short.wav` | TTS/降级预置音路径 |
| `server.resilience.use_tts_fallback_on_degrade` | true | 降级是否优先播预置音 |
| `server.registry.enabled` | true | Dialogue Redis 注册心跳；OTA 优先选存活实例 |
| `server.registry.instance_id` | （空） | 可选固定实例 ID；空则自动生成 |
| `server.registry.heartbeat_interval_seconds` | 30 | 心跳间隔（秒） |
| `server.registry.heartbeat_ttl_seconds` | 60 | 心跳 TTL（秒），过期=假存活 |
| `server.registry.redis.host` / `port` / `db` | 127.0.0.1 / 6379 / **0** | 须与 manager-api 同库（勿用熔断 db=1） |

切换：用 `data/.config.yaml.remote.bak` 覆盖为 `data/.config.yaml`，填 `manager-api.url` / `secret`，重启 manager-api（执行 Liquibase）与 xiaozhi-server。  
改参后可在【服务端管理】点「更新配置」；新上限对后续新连接生效。  
优先级：`config.yaml` 默认 < 智控台 API < `data/.config.yaml` 显式覆盖。

未配置时使用默认值（见 `ConnectionLimits.from_config`）。

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
| `config.yaml` / `data/.config.yaml` | `server.connection`、`server.metrics`、`server.resilience` |
| `core/utils/metrics.py` | **新建**：Prometheus 指标封装 |
| `core/utils/resilience.py` | **新建**：统一失败语义、熔断、降级播报 |
| `core/http_server.py` | 暴露 `/metrics` |
| `core/providers/asr/base.py` / `tts/base.py` | ASR/TTS 延迟与错误率；ASR 超时/重试/降级；TTS 重试/熔断 |
| `config/manage_api_client.py` | manager-api 重试叠加熔断 |
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
| `xiaozhi_chat_first_audio_seconds` | Histogram | chat 起表 → 首段可播音频发出（LLM 首包 + 攒到首句标点 + TTS） |
| `xiaozhi_queue_depth{queue}` | Gauge | `report` / `tts_text` / `tts_audio` 队列深度 |
| `xiaozhi_circuit_state{name}` | Gauge | 熔断状态 0=closed / 1=half_open / 2=open |
| `xiaozhi_degraded_total{stage,kind}` | Counter | 降级/预置音事件 |
| `xiaozhi_overload_shed_total{reason}` | Counter | 过载丢弃的新对话轮次 |

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

## 6.2 环境安全策略（部分落地）

通过 `server.environment`（或环境变量 `XIAOZHI_ENV` / `APP_ENV`）区分：

| 行为 | development | production |
|------|-------------|------------|
| URL query 传 `authorization` | 允许（打 warning） | 拒绝，仅 Header |
| 天气等硬编码 api_key 兜底 | 允许 | 禁止，未配置则失败 |
| 连接/OTA 认证 `auth.enabled` | 尊重配置（默认 false） | **强制开启**（除非 `auth.allow_insecure_disable=true`） |
| 白名单免检（跳过 token） | 默认允许（`allow_whitelist_bypass: auto`） | **默认禁止**；确需才显式 `true` |
| 仅白名单可接入 | 默认关 | 可选 `devices_allowlist_only: true` |
| AuthToken PBKDF2 盐 | 历史固定盐（兼容旧 token） | 由 `auth_key` 派生或 `auth.pbkdf2_salt` |
| query 传 `device-id` / `client-id` | 允许 | 允许（非密钥） |

配置示例：

```yaml
server:
  environment: development  # 上线改为 production
```

智控台参数：`server.environment`

---

## 6.3 已落地：依赖韧性（超时 / 重试 / 熔断 / 降级）

### 做了什么

- **统一失败语义**：`UpstreamError(stage, kind)`，含 timeout / overload / circuit_open 等
- **韧性包装**：超时 + 有限重试 + 熔断；`providers.<stage>.<name>` 可覆盖单 provider 策略
- **设备侧降级**：ASR/LLM/Tool 失败 → 话术或预置音；过载时新对话直接降级
- **TTS 预置音**：合成失败/熔断播本地文件
- **LLM fallback**：主模型未开口失败时切备用
- **单轮预算**：
  - `llm_ttfb_deadline_seconds`（默认 25s）：首 token，不是整轮 8s
  - `round_deadline_seconds`（默认 120s）：整轮含流式输出，超时收尾/降级
- **过载背压**：连接水位 / TTS 队列 / 上报队列超阈值 → `speak_degradation(overload)`
- **指标**：`xiaozhi_circuit_state`、`xiaozhi_degraded_total`、`xiaozhi_overload_shed_total`

文件：`core/utils/resilience.py`、`core/utils/metrics.py`；智控台：`202607311800/1830/1900.sql`。

### 配置示例

```yaml
server:
  resilience:
    enabled: true
    llm_ttfb_deadline_seconds: 25   # 首 token
    round_deadline_seconds: 120     # 整轮
    llm_fallback: DoubaoLLM
    tts_fallback_audio: config/assets/wakeup_words_short.wav
    use_tts_fallback_on_degrade: true
    overload:
      enabled: true
      max_concurrent_chats: 80      # 全局在途对话轮次
      max_concurrent_llm: 80        # 全局在途 LLM 流
      tts_text_queue_threshold: 80
    providers:
      llm:
        ChatGLMLLM: { timeout_seconds: 90 }
        default: { timeout_seconds: 60 }
    asr: "不好意思，我没听清楚，请再说一遍。"
    llm: "主人，小智现在有点忙，我们稍后再试吧。"
    overload: "现在有点忙不过来，请稍后再试一下。"
```

### 验证要点

1. 日志：`TTS 预置音` / `LLM 已切换到 fallback` / `过载降级` / `单轮预算耗尽`
2. `/metrics` 含 `xiaozhi_circuit_state`、`xiaozhi_degraded_total`、`xiaozhi_overload_shed_total`、`xiaozhi_inflight`
3. 主备皆失败或过载时设备仍能听到提示并正常 stop

### 本轮新增配置

```yaml
server:
  resilience:
    redis:
      enabled: false          # 多实例共享熔断
      host: 127.0.0.1
      port: 6379
      db: 1                   # 建议与 manager-api 的 0 隔离
      key_prefix: "xiaozhi:circuit:"
      fallback_local: true
```

---

## 6.5 已落地：Dialogue 注册心跳（多实例发现）

对齐 Java `RedisDialogueServerRegistry`：每个 `xiaozhi-server` 进程向 **与 manager-api 同库的 Redis** 注册自身 WebSocket 地址并定时心跳；OTA 优先从存活实例中随机选路，挂掉的实例因心跳 TTL 过期不再被抽中。

### 配置（`server.registry`）

智控台模式：在【参数管理】改 `server.registry.*`（见 §4.2），经 `/config/server-base` 下发。  
本地模式：写在 `data/.config.yaml` 的 `server.registry`。

```yaml
server:
  registry:
    enabled: true          # 多实例务必开启；单机无 Redis 可 false
    # instance_id: dialogue-1
    heartbeat_interval_seconds: 30
    heartbeat_ttl_seconds: 60
    redis:
      host: 127.0.0.1
      port: 6379
      password: ""
      db: 0                 # 必须与 manager-api 同库；勿用 resilience.redis.db=1
```

也可用环境变量 `XIAOZHI_INSTANCE_ID` 固定实例 ID。  
每个实例的 `server.websocket` 应配成**自己的**对外地址（不要把多机地址用 `;` 塞进同一进程）。

### Redis key

| Key | 说明 |
|-----|------|
| `xiaozhi:dialogue:servers` | Hash，field=`instanceId`，value=JSON |
| `xiaozhi:dialogue:heartbeat:{instanceId}` | String `"1"`，TTL=`heartbeat_ttl_seconds` |

### 行为

| 场景 | 行为 |
|------|------|
| 进程启动且 `enabled=true` | 注册 + 每 30s 心跳 |
| 进程退出 | 注销 Hash 字段与心跳 key |
| 心跳中断 > TTL | manager-api OTA 懒清理僵尸，不再选该实例 |
| 无存活注册实例 | OTA 回退静态 `server.websocket`（`;` 分隔随机） |

相关代码：`core/utils/dialogue_registry.py`、`app.py`；manager-api：`RedisDialogueServerRegistry`、`DeviceServiceImpl` OTA 选路。

单测：`tests/test_dialogue_registry.py`

---

## 7. 如何验证

1. **启动日志**应出现类似：  
   `运行环境: development`  
   `连接硬上限: max=500, per_device=2, report_queue=100`  
   `Prometheus metrics: http://0.0.0.0:8003/metrics`
2. **HTTP 访问 WS 端口**（非 Upgrade）应看到：  
   `active_connections=...` / `max_connections=...`
3. **压测同 device-id** 超过 `max_connections_per_device`：新连接被拒，日志含 `连接被拒绝`
4. **正常断开 / 超时断开**：日志出现 `连接资源已释放 session=... device=...`，连接水位回落
5. **重复触发关闭**（客户端断 + 服务端 finally）：不应出现成片二次清理异常
6. **curl 指标**：`curl http://127.0.0.1:8003/metrics | findstr xiaozhi`
7. **降级话术**：人为让 ASR/LLM 失败时，设备应听到 `server.resilience.*` 配置的短句，且 speaking 状态能正常 stop（日志含 `降级播报` / `会话降级`）
8. **注册心跳**：开启 `server.registry.enabled` 后启动日志含 `Dialogue 已注册到 Redis`；manager-api OTA GET 显示 `已注册存活实例：N`；停掉某实例约 60s 后 N 减少且不再被 OTA 抽中

---

## 8. 后续建议

生产级单体改造（上限 / 清理 / 可观测 / 安全 / 韧性）已完成。后续优先按**微服务拆分**推进，而不是先大拆 `ConnectionHandler`。

仍可按需补齐（与拆分并行即可）：

1. [x] **真 readiness**：`/health`（liveness）+ `/ready`（readiness，连接打满 / registry Redis 失败返回 503）——见 `core/utils/health.py`、[Production.md](../../docs/Production.md)
2. **网关单测（可选）**：连接准入/回收与限流全路径；核心策略单测与 CI 门禁已有
3. [x] **安全收尾**：production 默认禁止白名单免检；可选 `devices_allowlist_only`
4. **adapter 失败语义（渐进）**：各家 ASR/TTS/LLM 内源统一抛 `UpstreamError`（关键路径已接好）
5. **注册增强（可选）**：按连接水位加权选路、跨实例踢线（Java 侧有 device→instance 亲和可参考）

发布门禁：`.github/workflows/xiaozhi-server-tests.yml`；本地验证：`python scripts/production_verify.py`。  
运维入口：[docs/Production.md](../../docs/Production.md)（声明 / 探活 / 容量）。

不做：OTel / SkyWalking（已有 Prometheus；跨服务排障痛点再加）

---

## 9. 相关入口

- 主程序：`app.py`
- WebSocket 服务：`core/websocket_server.py`
- 单连接会话：`core/connection.py`
- 默认配置：`config.yaml`
- 从智控台拉配置：`config_from_api.yaml`（需自备 `data/.config.yaml`）

部署与功能集成文档见仓库 `docs/` 目录（如 [Deployment.md](../../docs/Deployment.md)）。
