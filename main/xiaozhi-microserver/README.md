# xiaozhi-microserver

本分支主运行时：将语音网关拆成 **Nacos + gRPC** 六微服务。设备侧地址：WebSocket `ws://host:8000/xiaozhi/v1/`，OTA `http://host:8003/xiaozhi/ota/`。

> 单体 `xiaozhi-server` 在**其他分支**维护；本分支不保留单体目录。

---

## 为什么做微服务

小智服务端的核心是 **有状态 WebSocket 长连接**：每台设备挂着 VAD / ASR / LLM / TTS、音频缓冲与会话状态。业务量上来后，单体进程会同时扛住连接、预处理、上游调用与控制面；这些能力的 **CPU / 内存 / 并发模型差异很大**。拆成微服务后可以：

| 诉求 | 微服务怎么满足 |
|------|----------------|
| **高并发接入** | `xiaozhi-access` 专注 WS 与连接上限，接入层与推理层解耦 |
| **弹性扩容** | 按瓶颈单独加副本：access / receiver / speaker / agent |
| **故障隔离** | 某一上游熔断或降级时，不必拖死全链路 |
| **独立发布** | 改 LLM / 插件不必重发整个语音网关；控制面与实时面分离 |

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

本地模型目录：`models/`（Silero VAD、SenseVoice 配置与分词器；`model.pt` 需另行下载）。

---

## 已具备的能力（摘要）

- 完整设备协议路径：listen / abort / MCP·IoT、VAD→ASR→LLM→TTS、AEC 参考音通道
- 可配置真实 provider（FunASR / Doubao / ChatGLM / EdgeTTS / Silero 等）
- 韧性：超时 / 重试 / 熔断与降级话术
- 可观测：access / admin 的 `/health` `/ready` `/metrics`
- 连接上限与生产门禁（`XIAOZHI_ENV=production` 时控制面需 token）
- 生产 compose：`docker-compose.prod.yml` + `deploy/production/`

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
    model_dir: ../models/snakers4_silero-vad
    threshold: 0.5
```

### ASR（`xiaozhi-audio-receiver/config.yaml`）

```yaml
selected_module:
  ASR: FunASR           # 或 Doubao / OpenAICompatASR
ASR:
  FunASR:
    type: fun_local
    model_dir: ../models/SenseVoiceSmall
```

FunASR 另需：`pip install -r requirements-funasr.txt`，并将 `model.pt` 放到 `models/SenseVoiceSmall/`。智控台下发的长名（如 `DoubaoASR`）会与短名镜像兼容。

**常用组合：** VAD=`SileroVAD`，ASR=`FunASR`，LLM=`ChatGLM`，TTS=`EdgeTTS`。

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
  models/                 # Silero / SenseVoice（model.pt 需下载）
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
| 6 | 韧性、指标、连接上限、生产 compose 与门禁；本分支删除单体目录 |
