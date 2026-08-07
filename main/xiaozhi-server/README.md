# xiaozhi-server 生产级改造说明

范围：`main/xiaozhi-server`。性质：有状态长连接网关（每设备一条 WebSocket，挂载 VAD / ASR / LLM / TTS、音频缓冲、线程池与上报队列）。

- 功能基线：Commit de45f73efdd24e9343427a56b5d22f857b6bb7a7（上游业务能力已具备）
- 目标态：Commit 7519dd516c79764eb722fe3c25239d6f30f665c8
- 关联：仓库根 README、main/README.md、docs/Production.md

本文档记录将 xiaozhi-server 从「功能可用」提升为「可自托管生产」过程中的问题诊断、改造优先级、配置约定与验证方法。业务功能继承自开源小智后端（华南理工大学刘思源教授团队主导研发及上游贡献者）；本文仅描述网关侧生产加固。

---

## 1. 设计目标

生产改造优先保证：将「每设备一条有状态长连接」约束为有上限、可回收、可观测、失败可隔离。

典型故障链：连接断开后资源未释放，导致内存 / 文件描述符 / 线程泄漏，进而进程不可用。代码结构优化与测试补全有价值，但不替代上述约束。

---

## 2. 问题清单

| 优先级 | 问题 | 风险 |
|--------|------|------|
| P0 | 无连接硬上限 | 突发连接打满进程，OOM 或事件循环阻塞 |
| P0 | 清理不完整、不幂等 | 多路径同时关闭时任务、线程、队列泄漏 |
| P1 | 可观测性不足 | 缺少连接水位、ASR/TTS/LLM 耗时与失败率、队列积压等指标 |
| P1 | 安全基线偏弱 | 认证可关闭、白名单可免检、token 可经 query 传递、部分硬编码密钥兜底 |
| P2 | 上游依赖缺少韧性 | ASR/TTS/LLM/manager-api 缺少统一超时、重试、熔断与设备侧降级 |
| P3 | ConnectionHandler 体量过大 | 约 1800 行，测试与演进成本高 |
| P3 | 自动化测试不足 | 回归依赖手工，变更风险高 |

---

## 3. 改造路线图

| 序号 | 项 | 状态 |
|------|----|------|
| 1 | 硬上限：全局并发、同设备并发、上报队列有界 | 已完成 |
| 2 | 确定性清理：幂等 close、任务登记与取消、固定顺序、清理超时 | 已完成 |
| 3 | 可观测性：连接水位、会话生命周期、provider 延迟与错误率、队列深度（Prometheus） | 已完成 |
| 4 | 安全基线：development 保留联调兼容；production 禁止 query token、禁止硬编码 key 兜底、默认强制认证、默认禁止白名单免检 | 已完成 |
| 5 | 依赖韧性：统一失败语义、有限重试、熔断、设备侧降级 | 已完成 |
| 6 | Dialogue 注册心跳：多实例 Redis 注册，OTA 优先选存活实例 | 已完成 |
| 7 | 发布门禁：session / resilience / registry / health / runtime_env 单测与 CI | 已完成（连接准入全路径单测仍可选） |

CI 工作流：`.github/workflows/xiaozhi-server-tests.yml`

---

## 4. 连接硬上限

### 4.1 行为

在创建业务 Handler 之前进行准入校验。

| 项 | 说明 |
|----|------|
| 实现 | core/connection_registry.py |
| 接入 | core/websocket_server.py |
| 超限关闭码 | 1013（Try Again Later） |
| HTTP 水位 | 访问 WebSocket 端口的普通 HTTP 请求返回 active_connections / max_connections（非完整 readiness） |

### 4.2 配置

本地模式（无智控台）：写入 data/.config.yaml 的 server.connection 等段。

智控台模式：参数写入 sys_params，可在【参数管理】修改。切换远程配置时，可用 data/.config.yaml.remote.bak 覆盖为 data/.config.yaml，填写 manager-api.url / secret，并重启 manager-api（执行 Liquibase）与 xiaozhi-server。修改后可在【服务端管理】执行「更新配置」；新上限对后续新连接生效。

配置优先级：config.yaml 默认，低于智控台 API，低于 data/.config.yaml 显式覆盖。未配置时使用 ConnectionLimits.from_config 默认值。

| param_code | 默认 | 说明 |
|------------|------|------|
| server.connection.max_connections | 200 | 单进程最大 WebSocket 连接数 |
| server.connection.max_connections_per_device | 2 | 同一 device-id 上限 |
| server.connection.report_queue_maxsize | 100 | 上报队列容量 |
| server.connection.cleanup_timeout_seconds | 10 | 清理超时（秒） |
| server.metrics.enabled | true | 是否启用 Prometheus /metrics |
| server.metrics.path | /metrics | 指标路径（挂载于 http_port） |
| server.resilience.enabled | true | 是否启用上游韧性 |
| server.resilience.max_retries | 2 | 瞬时故障最大重试次数 |
| server.resilience.asr_timeout_seconds | 15 | ASR 单次超时（秒） |
| server.resilience.tts_max_retries | 3 | TTS 合成最大重试次数 |
| server.resilience.circuit_failure_threshold | 5 | 熔断连续失败阈值 |
| server.resilience.circuit_open_seconds | 30 | 熔断开路冷却（秒） |
| server.resilience.asr / llm / tts / tool | 见 config.yaml | 各阶段降级话术 |
| server.resilience.llm_fallback | （空） | 备用 LLM 配置名（LLM 段键） |
| server.resilience.tts_fallback_audio | config/assets/wakeup_words_short.wav | TTS / 降级预置音路径 |
| server.resilience.use_tts_fallback_on_degrade | true | 降级时是否优先播放预置音 |
| server.registry.enabled | true | Dialogue Redis 注册心跳；OTA 优先选存活实例 |
| server.registry.instance_id | （空） | 固定实例 ID；空则自动生成 |
| server.registry.heartbeat_interval_seconds | 30 | 心跳间隔（秒） |
| server.registry.heartbeat_ttl_seconds | 60 | 心跳 TTL（秒）；过期视为不存活 |
| server.registry.redis.host / port / db | 127.0.0.1 / 6379 / 0 | 须与 manager-api 同库（勿使用熔断用的 db=1） |

### 4.3 场景行为

| 场景 | 行为 |
|------|------|
| 全局连接数达到 max_connections | 拒绝新连接，记录 warning，关闭码 1013 |
| 同 device-id 连接数达到上限 | 同上 |
| 连接正常或异常结束 | 在 finally 中 release，名额幂等回收 |
| 访问 WebSocket 端口的普通 HTTP | 返回当前连接水位 |

建议按机器规格调整 max_connections；单设备并发通常保持为 1 或 2。

---

## 5. 确定性清理

### 5.1 要求

设备断开后的资源释放须满足：

1. 固定清理顺序
2. 覆盖会话相关资源，避免遗漏
3. 幂等：多次调用 close() 效果与一次相同
4. 单步超时，避免 close() 长时间阻塞事件循环

幂等通过 _closing / _closed 标志实现。断开可能由客户端关闭、finally、空闲超时或服务端强制关闭等多路径触发；无幂等时易出现竞态或对已释放对象的重复操作。

### 5.2 实现要点

会话状态与任务登记（ConnectionHandler.__init__）：

| 变量 | 作用 |
|------|------|
| _closing | 正在关闭 |
| _closed | 已关闭完成 |
| _tracked_tasks | 本会话创建的 asyncio 任务集合 |
| _cleanup_timeout_seconds | 清理步骤超时 |

上报队列：由无界 queue.Queue() 改为 maxsize=report_queue_maxsize；队列满时丢弃（见 reportHandle._enqueue_report）。

任务生命周期：

- 通过 spawn_task 创建并登记到 _tracked_tasks；关闭流程中拒绝新建任务。
- 已覆盖：超时检查、AEC 缓存清理、后台初始化、绑定提示、VAD resume 等。
- _cancel_tracked_tasks：关闭时取消未完成任务并带超时等待；不取消正在执行 close 的任务本身。

辅助方法：

| 方法 | 作用 |
|------|------|
| _drain_queue | 非阻塞清空队列残留 |
| _stop_report_thread | 投递结束哨兵并 join，停止上报线程 |
| _safe_close_websocket | 已关闭则跳过；失败不中断整体清理 |

close() 顺序：

| 步骤 | 动作 |
|------|------|
| 守卫 | 已关闭或正在关闭则直接返回 |
| 1 | stop_event.set()，中断后续业务 |
| 2 | 取消会话级后台任务 |
| 3 | 释放 VAD 连接资源 |
| 4 | 清理 opus 解码器与音频缓冲 / ASR 队列 |
| 5 | 清理 AEC 缓存 |
| 6 | 工具处理器 cleanup（带超时） |
| 7 | 清空业务队列并停止上报线程 |
| 8 | 关闭 WebSocket |
| 9 | 关闭 TTS / ASR 上游（带超时） |
| 10 | executor.shutdown |
| finally | _closed = True |

空闲超时：仅设置 stop_event 并关闭 WebSocket；完整清理统一由 handle_connection 的 finally 到 _save_and_close 再到 close() 执行，避免超时任务内直接 await close() 与任务取消相互干扰。

---

## 6. 关键文件

| 文件 | 变更 |
|------|------|
| core/connection_registry.py | 新建：连接硬上限与会话登记 |
| core/websocket_server.py | 准入校验、登记/释放、水位提示、配置热更新同步 limits |
| core/connection.py | 幂等 close、任务跟踪、有界上报队列、确定性清理 |
| core/handle/reportHandle.py | put_nowait；队列满时丢弃 |
| core/handle/receiveAudioHandle.py | VAD resume 经 spawn_task |
| config.yaml / data/.config.yaml | server.connection、server.metrics、server.resilience、server.registry |
| core/utils/metrics.py | 新建：Prometheus 指标封装 |
| core/utils/resilience.py | 新建：统一失败语义、熔断、降级播报 |
| core/utils/dialogue_registry.py | Dialogue Redis 注册心跳 |
| core/utils/health.py | /health、/ready |
| core/http_server.py | 暴露 /metrics 与探活 |
| core/providers/asr/base.py / tts/base.py | 延迟与错误率；超时/重试/降级/熔断 |
| config/manage_api_client.py | manager-api 重试与熔断 |
| scripts/ws_lifecycle_smoke.py | 连接生命周期冒烟 |
| scripts/production_verify.py | 生产自检脚本 |

---

## 7. 可观测性

### 7.1 指标

| 指标 | 类型 | 含义 |
|------|------|------|
| xiaozhi_ws_active_connections | Gauge | 当前活跃 WebSocket 会话 |
| xiaozhi_ws_max_connections | Gauge | 配置的全局上限 |
| xiaozhi_ws_rejected_total | Counter | 硬上限拒绝（label: reason） |
| xiaozhi_ws_sessions_opened_total | Counter | 成功准入会话数 |
| xiaozhi_ws_session_duration_seconds | Histogram | 会话存活时长 |
| xiaozhi_provider_requests_total | Counter | ASR/TTS/LLM 请求（labels: component, provider, status） |
| xiaozhi_provider_latency_seconds | Histogram | 端到端耗时 |
| xiaozhi_provider_ttfb_seconds | Histogram | LLM 首 token 时延 |
| xiaozhi_chat_first_audio_seconds | Histogram | 对话开始至首段可播音频发出 |
| xiaozhi_queue_depth | Gauge | report / tts_text / tts_audio 队列深度 |
| xiaozhi_circuit_state | Gauge | 熔断状态：0=closed，1=half_open，2=open |
| xiaozhi_degraded_total | Counter | 降级 / 预置音事件 |
| xiaozhi_overload_shed_total | Counter | 过载丢弃的新对话轮次 |

### 7.2 配置

```yaml
server:
  metrics:
    enabled: true
    path: /metrics
```

拉取地址：http://主机:http_port/metrics（默认端口 8003）。依赖：prometheus_client（见 requirements.txt）。

探活：

| 路径 | 用途 |
|------|------|
| GET /health | 存活探测 |
| GET /ready | 就绪探测；连接打满或 registry Redis 不可用时返回 503 |

---

## 8. 环境安全策略

通过 server.environment（或环境变量 XIAOZHI_ENV / APP_ENV）区分：

| 行为 | development | production |
|------|-------------|------------|
| URL query 传递 authorization | 允许（记录 warning） | 拒绝，仅允许 Header |
| 硬编码 api_key 兜底（如天气插件） | 允许 | 禁止；未配置则失败 |
| 连接 / OTA 认证 auth.enabled | 尊重配置（默认 false） | 强制开启（除非 auth.allow_insecure_disable=true） |
| 白名单免检（跳过 token） | 默认允许（allow_whitelist_bypass: auto） | 默认禁止；确需时显式设为 true |
| 仅白名单可接入 | 默认关闭 | 可选 devices_allowlist_only: true |
| AuthToken PBKDF2 盐 | 历史固定盐（兼容旧 token） | 由 auth_key 派生或 auth.pbkdf2_salt |
| query 传递 device-id / client-id | 允许 | 允许（非密钥） |

```yaml
server:
  environment: development  # 上线改为 production
```

智控台参数码：server.environment。

---

## 9. 依赖韧性

### 9.1 能力

| 能力 | 说明 |
|------|------|
| 统一失败语义 | UpstreamError(stage, kind)，含 timeout / overload / circuit_open 等 |
| 韧性包装 | 超时、有限重试、熔断；可按 providers.stage.name 覆盖单 provider |
| 设备侧降级 | ASR / LLM / Tool 失败时播放话术或预置音；过载时新对话直接降级 |
| TTS 预置音 | 合成失败或熔断时播放本地音频文件 |
| LLM fallback | 主模型在开口前失败时切换备用配置 |
| 单轮预算 | llm_ttfb_deadline_seconds（默认 25s，首 token）；round_deadline_seconds（默认 120s，整轮） |
| 过载背压 | 连接水位 / TTS 队列 / 上报队列超阈值时 speak_degradation(overload) |

实现：core/utils/resilience.py、core/utils/metrics.py。智控台相关变更见 Liquibase：202607311800 / 1830 / 1900。

### 9.2 配置示例

```yaml
server:
  resilience:
    enabled: true
    llm_ttfb_deadline_seconds: 25
    round_deadline_seconds: 120
    llm_fallback: DoubaoLLM
    tts_fallback_audio: config/assets/wakeup_words_short.wav
    use_tts_fallback_on_degrade: true
    overload:
      enabled: true
      max_concurrent_chats: 80
      max_concurrent_llm: 80
      tts_text_queue_threshold: 80
    providers:
      llm:
        ChatGLMLLM: { timeout_seconds: 90 }
        default: { timeout_seconds: 60 }
    asr: "不好意思，我没听清楚，请再说一遍。"
    llm: "服务暂时繁忙，请稍后再试。"
    overload: "当前请求较多，请稍后再试。"
    redis:
      enabled: false
      host: 127.0.0.1
      port: 6379
      db: 1
      key_prefix: "xiaozhi:circuit:"
      fallback_local: true
```

### 9.3 验证要点

1. 日志出现预置音播放、LLM fallback、过载降级或单轮预算耗尽等记录。
2. /metrics 包含 xiaozhi_circuit_state、xiaozhi_degraded_total、xiaozhi_overload_shed_total、xiaozhi_inflight。
3. 主备均失败或过载时，设备仍能收到提示音，speaking 状态可正常结束。

---

## 10. Dialogue 注册心跳

对齐 manager-api 侧 RedisDialogueServerRegistry：每个 xiaozhi-server 进程向与 manager-api 同一 Redis 库注册自身 WebSocket 地址并定时心跳；OTA 优先从存活实例中选路，心跳 TTL 过期的实例不再被选中。

### 10.1 配置

智控台模式：在【参数管理】修改 server.registry.*（见第 4.2 节），经 /config/server-base 下发。
本地模式：写入 data/.config.yaml 的 server.registry。

```yaml
server:
  registry:
    enabled: true
    heartbeat_interval_seconds: 30
    heartbeat_ttl_seconds: 60
    redis:
      host: 127.0.0.1
      port: 6379
      password: ""
      db: 0
```

实例 ID 也可通过环境变量 XIAOZHI_INSTANCE_ID 固定。每个实例的 server.websocket 应配置为该实例自身的对外地址，勿将多机地址以分号写入同一进程。

### 10.2 Redis Key

| Key | 说明 |
|-----|------|
| xiaozhi:dialogue:servers | Hash，field=instanceId，value=JSON |
| xiaozhi:dialogue:heartbeat:INSTANCE | String 1，TTL=heartbeat_ttl_seconds |

### 10.3 行为

| 场景 | 行为 |
|------|------|
| 进程启动且 enabled=true | 注册，并按间隔心跳 |
| 进程退出 | 注销 Hash 字段与心跳 key |
| 心跳中断超过 TTL | manager-api OTA 侧清理僵尸实例，不再选中 |
| 无存活注册实例 | OTA 回退静态 server.websocket（分号分隔随机） |

相关代码：core/utils/dialogue_registry.py、app.py；manager-api：RedisDialogueServerRegistry、DeviceServiceImpl OTA 选路。单测：tests/test_dialogue_registry.py。

---

## 11. 验证清单

| 序号 | 检查项 | 期望 |
|------|--------|------|
| 1 | 启动日志 | 含运行环境、连接硬上限、Prometheus 地址；多实例时含 Dialogue 注册成功 |
| 2 | WebSocket 端口普通 HTTP | 返回 active_connections / max_connections |
| 3 | 同 device-id 超并发 | 新连接被拒绝，日志含拒绝原因 |
| 4 | 正常或超时断开 | 日志含资源已释放，连接水位回落 |
| 5 | 多路径重复关闭 | 无成片二次清理异常 |
| 6 | GET /metrics | 指标名含 xiaozhi_ |
| 7 | 人为制造 ASR/LLM 失败 | 设备播放降级话术或预置音，speaking 可正常结束 |
| 8 | 注册心跳 | OTA 显示存活实例数；停实例约一个 TTL 后不再被选中 |
| 9 | python scripts/production_verify.py | 自检通过 |

运维声明、探活与容量说明见 docs/Production.md。

---

## 12. 后续工作

单体侧的上限、清理、可观测、安全与韧性改造已完成。后续优先推进微服务拆分（xiaozhi-microserver/），与下列可选项并行：

| 项 | 状态 | 说明 |
|----|------|------|
| /health 与 /ready | 已完成 | 见 core/utils/health.py、docs/Production.md |
| production 白名单默认策略 | 已完成 | 默认禁止免检；可选 devices_allowlist_only |
| 连接准入全路径单测 | 可选 | 核心策略单测与 CI 已存在 |
| adapter 统一抛出 UpstreamError | 渐进 | 关键路径已接入 |
| 注册加权选路 / 跨实例踢线 | 可选 | 可参考 Java 侧 device 到 instance 亲和 |

发布门禁：.github/workflows/xiaozhi-server-tests.yml。本地验证：python scripts/production_verify.py。

当前不引入 OpenTelemetry / SkyWalking；在已有 Prometheus 指标无法满足跨服务排障前不扩展链路追踪。

---

## 13. 代码入口

| 路径 | 说明 |
|------|------|
| app.py | 进程入口 |
| core/websocket_server.py | WebSocket 服务与准入 |
| core/connection.py | 单连接会话与清理 |
| config.yaml | 默认配置（主线多为 development） |
| config_from_api.yaml | 智控台拉配置模板（需自备 data/.config.yaml） |
| deploy/production/ | 生产 compose overlay |

部署与功能集成见仓库 docs 目录（如 docs/Deployment.md）。