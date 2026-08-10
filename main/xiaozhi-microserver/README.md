# xiaozhi-microserver

将开源单体 [`xiaozhi-server`](../xiaozhi-server) 拆成 **Nacos + gRPC** 六微服务后的实现。设备侧地址与开源单体对齐：WebSocket `ws://host:8000/xiaozhi/v1/`，OTA `http://host:8003/xiaozhi/ota/`。

---

## 为什么做微服务分支

小智服务端的核心是 **有状态 WebSocket 长连接**：每台设备挂着 VAD / ASR / LLM / TTS、音频缓冲与会话状态。业务量上来后，单体进程会同时扛住：

- 连接数与协议解复用
- 语音预处理（解码 / VAD / AEC）
- ASR、LLM、TTS 等上游调用
- 控制面（OTA、配置、智控台对接）

这些能力的 **CPU / 内存 / 并发模型差异很大**。拆成微服务后可以：

| 诉求 | 微服务怎么满足 |
|------|----------------|
| **高并发接入** | `xiaozhi-access` 专注 WS 与连接上限，接入层与推理层解耦，避免「ASR/TTS 拖垮整机」 |
| **弹性扩容** | 按瓶颈单独加副本：接入多扩 access，识别多扩 receiver，合成多扩 speaker，对话多扩 agent |
| **故障隔离** | 某一上游（如 TTS）熔断或降级时，不必拖死全链路；韧性策略可按服务配置 |
| **独立发布** | 改 LLM / 插件不必重发整个语音网关；控制面与实时面分离 |

本仓库的六服务拆分、真实 provider、Abort/AEC、韧性与生产 compose 等能力已按规划落地，可作为 **规模化部署** 的参考实现。

---

## 不一定要用微服务

**多数刚起步的团队，更建议继续用单体 [`xiaozhi-server`](../xiaozhi-server)。**

| | 单体 `xiaozhi-server` | 微服务 `xiaozhi-microserver` |
|--|----------------------|------------------------------|
| 适合阶段 | 验证产品、小规模设备、少人运维 | 设备量大、峰值明显、需按模块扩容 |
| 部署 | 一个（加智控台）进程即可 | 六进程 + 发现 / 配置 / 链路排障 |
| 维护成本 | 低：日志、版本、依赖集中 | 高：跨服务契约、联调、观测都要跟上 |
| 性能天花板 | 单机有上限，但多数早期场景够用 | 按服务水平扩展，理论上限更高 |

说明几点：

1. **微服务不是默认选项。** 拆分换来的是扩展性与隔离，代价是运维与复杂度；业务未到瓶颈时，这笔账通常不划算。
2. **单体也可以横向扩展。** 多实例 + 负载均衡（设备连不同 WS 节点）、对接智控台 / Redis 注册与 OTA 选活实例等，单体同样能撑住一定规模的并发，不必一上来上微服务。
3. **两条线并存。** 开发联调、小客户交付优先单体；明确要按模块弹性扩容、或接入层与推理层必须隔离时，再切本分支。

选型一句话：**先跑通单体；真有高并发与弹性扩容诉求，再上微服务。**

---

## 架构一览

```
Device --OTA--> control-admin
Device --WS--> access --gRPC--> preprocess (VAD/AEC) --gRPC--> receiver (ASR)
                              |--gRPC--> agent (LLM / tools / Session)
                              |            |--SpeakText--> speaker
                              |            |--SendToDevice--> access --WS--> Device
                              |--HandleDeviceEvent (mcp/iot)
                              |--Abort --> agent + speaker + preprocess
speaker --PushAecReference--> preprocess
control-admin --ApplyConfig / BroadcastSpeak--> access / speaker
```

聊天历史上报（可选）：`xiaozhi-agent` → RabbitMQ → **manager-api** 消费落库；`control-admin` 不参与该队列。

### 六个服务

| 目录 | Nacos 名 | Dev 端口 | 职责 |
|------|----------|----------|------|
| `xiaozhi-access` | `xiaozhi-access-grpc-service` | HTTP **8000** / gRPC **50051** | 设备 WS、鉴权、连接上限、协议解复用、会话状态、下行回写；上行转 preprocess；MCP/IoT 代理 |
| `xiaozhi-agent` | `xiaozhi-agent-grpc-service` | gRPC **50052** | Intent / LLM / Memory / 工具插件；句子流给 speaker |
| `xiaozhi-audio-speaker` | `xiaozhi-audio-speaker-grpc-service` | gRPC **50053** | TTS、分句队列、rate controller；经 access 写回；AEC 参考音 |
| `xiaozhi-audio-preprocess` | `xiaozhi-audio-preprocess-grpc-service` | gRPC **50054** | 解码 / VAD / AEC；调 ASR；听状态；文本投递 agent |
| `xiaozhi-audio-receiver` | `xiaozhi-audio-receiver-grpc-service` | gRPC **50055** | ASR（含声纹扩展） |
| `xiaozhi-control-admin` | `xiaozhi-control-admin-grpc-service` | HTTP **8003** / gRPC **50056** | 配置热更、manager-api、OTA / 视觉 / health / metrics、`broadcast_speak` |

公共库：`common/xiaozhi_common`（配置、Nacos、gRPC、session、auth、resilience、metrics）+ `common/proto`。

智控台 `manager-api` / `manager-web` **不拆进**这六服务，由 `xiaozhi-control-admin` 对接。

### 相对单体的模块映射

| 原 `xiaozhi-server` | 目标服务 |
|---------------------|----------|
| WS / 鉴权 / 连接登记 / 协议与会话 | `xiaozhi-access` |
| VAD / 收音处理 | `xiaozhi-audio-preprocess` |
| ASR / 声纹 | `xiaozhi-audio-receiver` |
| LLM / Intent / Memory / tools / plugins | `xiaozhi-agent` |
| TTS / 下行音频 / rate controller | `xiaozhi-audio-speaker` |
| HTTP OTA·vision / 配置 / health·metrics | `xiaozhi-control-admin` |

---

## 已具备的能力（摘要）

- 完整设备协议路径：listen / abort / MCP·IoT、VAD→ASR→LLM→TTS、AEC 参考音通道
- 可配置真实 provider（FunASR / Doubao / ChatGLM / EdgeTTS / Silero 等）
- 韧性：超时 / 重试 / 熔断与降级话术
- 可观测：access / admin 的 `/health` `/ready` `/metrics`
- 连接上限与生产门禁（`XIAOZHI_ENV=production` 时控制面需 token）
- 生产 compose：`docker-compose.prod.yml` + `deploy/production/`

更细的分期说明见文末「演进回顾」。

---

## 快速开始

```bash
cd xiaozhi-esp32-server/main/xiaozhi-microserver
pip install -r requirements.txt
python common/generate_proto.py   # 修改 proto 后重新生成

# Windows
start_dev_services.bat

# Linux / macOS
bash start_dev_services.sh

# 冒烟（需六服务已起）
python scripts/ws_smoke.py
python scripts/ws_smoke.py --abort
python scripts/agent_smoke.py --mcp --iot --exit
python scripts/ota_smoke.py

pip install pytest
python scripts/run_tests.py
```

默认 `--disable_nacos`，用静态端口互发现。有 Nacos 时去掉该参数，并配置 `--nacos_host` / `--group_name` / `--env_id`。可用 `--peer name=host:port` 覆盖发现。

### 探活与联调入口

- Access：`http://127.0.0.1:8000/health` 、`/ready` 、`/metrics`
- control-admin：`http://127.0.0.1:8003/health` 、`/config` ；OTA `POST /xiaozhi/ota/`；视觉 `/mcp/vision/explain`
- WS：`ws://127.0.0.1:8000/xiaozhi/v1/?device-id=test-001`  
  例：`{"type":"listen","state":"detect","text":"现在几点了"}`

### 生产 compose

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

详见 [docs/Production.md](../../docs/Production.md)。

### 聊天历史上报（RabbitMQ）

`xiaozhi-agent` 发布 → 队列 `xiaozhi.chat.history` → **manager-api** 消费落库。默认可由参数管理 `rabbitmq.enabled` 开关；改参后需「更新配置」并重启 manager-api。连接信息见 `xiaozhi-agent/config.yaml` 与 manager-api `application.yml`。

---

## 切换真实模型（常用）

### LLM（`xiaozhi-agent/config.yaml`）

```yaml
selected_module:
  LLM: ChatGLM          # 或 Doubao / OpenAICompatLLM 等
LLM:
  ChatGLM:
    type: openai
    model_name: glm-4-flash
    url: https://open.bigmodel.cn/api/paas/v4/
    api_key: "你的key"
```

### TTS（`xiaozhi-audio-speaker/config.yaml`）

```yaml
selected_module:
  TTS: EdgeTTS          # 或 Doubao / DoubaoTTS
TTS:
  EdgeTTS:
    type: edge
    voice: zh-CN-XiaoxiaoNeural
```

### VAD（`xiaozhi-audio-preprocess/config.yaml`）

```yaml
selected_module:
  VAD: SileroVAD
VAD:
  SileroVAD:
    type: silero
    model_dir: ../../xiaozhi-server/models/snakers4_silero-vad
    threshold: 0.5
```

### ASR（`xiaozhi-audio-receiver/config.yaml`）

```yaml
selected_module:
  ASR: FunASR           # 或 Doubao / OpenAICompatASR
ASR:
  FunASR:
    type: fun_local
    model_dir: ../../xiaozhi-server/models/SenseVoiceSmall
```

FunASR 另需：`pip install -r requirements-funasr.txt`。智控台下发的长名（如 `DoubaoASR`）会与短名镜像兼容。

**对齐单体常用组合：** VAD=`SileroVAD`，ASR=`FunASR`，LLM=`ChatGLM`，TTS=`EdgeTTS`。

---

## Proto 与目录

| Proto | 用途 |
|-------|------|
| `audio.proto` | 音频 / ASR / TTS / Agent 设备事件与 Abort 等 |
| `command.proto` | 听状态、设备指令、连接统计、`SendToDevice` |
| `session.proto` | Abort / Ping |
| `admin.proto` | 配置拉取、热更、`ApplyConfig` |

```
xiaozhi-microserver/
  common/                 # proto + xiaozhi_common
  xiaozhi-access/
  xiaozhi-agent/
  xiaozhi-audio-preprocess/
  xiaozhi-audio-receiver/
  xiaozhi-audio-speaker/
  xiaozhi-control-admin/
  deploy/production/
  scripts/  tests/
  docker-compose.yml  docker-compose.prod.yml
```

---

## 演进回顾（已完成）

| 期 | 内容 |
|----|------|
| 1 | 骨架、契约、假链路联调 |
| 2 | 控制面 + 真实网关协议、配置热更 |
| 3 | Agent（LLM / 插件 / Session API） |
| 4 | TTS 与 broadcast / abort |
| 5 | VAD / ASR / AEC |
| 6 | 韧性、指标、连接上限、生产 compose 与门禁 |

若你仍在评估是否上微服务，请先回到本文开头的选型说明，再决定是否投入本分支的运维成本。
