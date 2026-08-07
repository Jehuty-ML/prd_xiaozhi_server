# xiaozhi-microserver

将开源单体 [`xiaozhi-server`](../xiaozhi-server) 按 Nacos + gRPC 微服务架构拆分后的实现（**第四期：TTS — providers / 分句队列 / rate controller / abort / broadcast**）。

> 参考架构：仓库旁 `micro_service` 的进程划分与通信方式。  
> 命名：全部使用 **xiaozhi** 语义，不引入专有业务名词。

## 六个微服务

| 目录 | Nacos 名 | Dev 端口 | 职责 |
|------|----------|----------|------|
| `xiaozhi-access` | `xiaozhi-access-grpc-service` | HTTP **8103** / gRPC **50051** | 设备 WebSocket 入口、鉴权、连接上限、协议解复用、会话状态、下行 TTS/指令回写；上行转 preprocess；MCP/IoT 代理到 agent |
| `xiaozhi-agent` | `xiaozhi-agent-grpc-service` | gRPC **50052** | Intent / LLM / Memory / 工具插件 / MCP·IoT（经 access 代理）；句子流给 speaker |
| `xiaozhi-audio-speaker` | `xiaozhi-audio-speaker-grpc-service` | gRPC **50053** | TTS 合成与分句队列、rate controller；经 access `SendTtsAudio` 写回设备 |
| `xiaozhi-audio-preprocess` | `xiaozhi-audio-preprocess-grpc-service` | gRPC **50054** | 解码 / VAD 分句；调 receiver ASR；通知 access 听状态；投递文本给 agent |
| `xiaozhi-audio-receiver` | `xiaozhi-audio-receiver-grpc-service` | gRPC **50055** | ASR：PCM → 文本（含声纹扩展位） |
| `xiaozhi-model-admin` | `xiaozhi-model-admin-grpc-service` | HTTP **8004** / gRPC **50056** | 配置 / 热更新广播 / manager-api 对接 / OTA·视觉·health·metrics；`broadcast_speak` 控制面 |

公共库：`common/xiaozhi_common`（配置、Nacos、gRPC、session DTO、auth / runtime_env）+ `common/proto`（统一契约）。

> 并存说明：原单体常用 HTTP `8003`，本仓库 access 默认用 **8103**，避免与仍在运行的 `xiaozhi-server` 冲突。删除单体后可改回 8003。

## 第四期能力（当前）

- **TTS**：`EchoTTS`（默认，静音 Opus 帧）+ `EdgeTTS`（在 `xiaozhi-audio-speaker/config.yaml` 切换）
- **分句队列 / rate controller**：每 client 独立 `SpeakSession`；约 60ms 帧 pacing 经 `SendTtsAudio` 下行
- **设备协议**：`tts` JSON `state=start|sentence_start|stop` + 二进制 Opus 帧
- **Abort**：WS abort 扇出到 `Agent.Abort` + `Speaker.Abort`（清空 speaker 队列）
- **broadcast_speak**：admin HTTP `POST /broadcast_speak` 或 WS `type=server,action=broadcast_speak` → speaker → access
- **Agent**：`Session.speak` / `speak_end`；对话结束发 `end=true` 触发 stop
- VAD / ASR 仍为假实现（第五期）

```
Device --OTA--> model-admin
Device --WS--> access --gRPC--> preprocess --gRPC--> receiver (ASR stub)
                              |--gRPC--> agent (LLM / tools / Session)
                              |            |--SpeakText--> speaker (EchoTTS / EdgeTTS)
                              |            |--SendToDevice--> access --WS--> Device
                              |--HandleDeviceEvent (mcp/iot)
                              |--Abort --> agent + speaker
model-admin --ApplyConfig--> access
model-admin --BroadcastSpeak--> speaker --SendTtsAudio--> access
```

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

# 单元测试 + 冒烟一键跑
pip install pytest
python scripts/run_tests.py
python scripts/run_tests.py --unit-only
```

默认带 `--disable_nacos`，用静态端口互发现。有 Nacos 时去掉该参数，并配置 `--nacos_host` / `--group_name` / `--env_id`。

可用 `--peer name=host:port` 覆盖发现地址。

### 切换真实 LLM

编辑 `xiaozhi-agent/config.yaml`：

```yaml
selected_module:
  LLM: OpenAICompatLLM
LLM:
  OpenAICompatLLM:
    type: openai
    model_name: glm-4-flash
    url: https://open.bigmodel.cn/api/paas/v4/
    api_key: "你的key"
```

重启 `xiaozhi-agent` 即可。

### 切换真实 TTS（Edge）

编辑 `xiaozhi-audio-speaker/config.yaml`：

```yaml
selected_module:
  TTS: EdgeTTS
TTS:
  EdgeTTS:
    type: edge
    voice: zh-CN-XiaoxiaoNeural
```

重启 `xiaozhi-audio-speaker`。需本机可访问 Edge TTS，并已安装 `edge-tts` / `pydub` / `opuslib_next`（见 `requirements.txt`；解码 mp3 通常还需 ffmpeg）。

### 手动探活

- Access health / ready: `http://127.0.0.1:8103/health` 、`/ready`
- Model-admin: `http://127.0.0.1:8004/health` 、`/ready` 、`/metrics` 、`/config`
- OTA: `POST http://127.0.0.1:8004/xiaozhi/ota/`（header: `device-id` / `client-id`）
- Vision: `GET|POST http://127.0.0.1:8004/mcp/vision/explain`
- Broadcast: `POST http://127.0.0.1:8004/broadcast_speak` body `{"text":"全员播报测试"}`
- WS: `ws://127.0.0.1:8103/xiaozhi/v1/?device-id=test-001`  
  发送：`{"type":"listen","state":"detect","text":"现在几点了"}`，应收到 `type=tts`（`state=start` / `sentence_start` / Opus / `stop`）。
- 热更新：`POST http://127.0.0.1:8004/config/reload`（会广播到 access）

## 与单体模块映射

| 原 `xiaozhi-server` | 目标服务 |
|---------------------|----------|
| `websocket_server` / `connection_registry` / `auth` / text·abort·hello handlers / `session_state` | `xiaozhi-access` |
| `providers/vad` / `receiveAudioHandle` | `xiaozhi-audio-preprocess` |
| `providers/asr` / voiceprint | `xiaozhi-audio-receiver` |
| `chat` / `llm` / `intent` / `memory` / `tools` / `plugins_func` | `xiaozhi-agent` |
| `providers/tts` / `sendAudioHandle` / `audioRateController` | `xiaozhi-audio-speaker` |
| `http_server` / OTA·vision / `config/*` / `modules_initialize` / health·metrics | `xiaozhi-model-admin` |

`manager-api` / `manager-web` **不拆进**这六服务，由 `xiaozhi-model-admin` 对接。

## Proto

| 文件 | 内容 |
|------|------|
| `common/proto/audio.proto` | `SendAudioChunk` / `AsrRecognize` / `SendText` / `SpeakText` / `SendTtsAudio`；Agent `HandleDeviceEvent` / `Abort`；Speaker `Abort` / `BroadcastSpeak`；`TtsAudioRequest.state` |
| `common/proto/command.proto` | listen 状态、设备指令、`GetConnectionStats` / `ListClients`；`AccessDeviceProxyService.SendToDevice` |
| `common/proto/session.proto` | Abort / Ping（access 本地 + agent Abort） |
| `common/proto/admin.proto` | GetConfig / ReloadConfig / Health；`ConfigApplyService.ApplyConfig` 热更新广播 |

## 演进路线

### 第一期（完成）

骨架、统一 proto、Nacos/静态发现、假 ASR/LLM/TTS 端到端联调。  
**不删除** `xiaozhi-server`，两边并存。

### 第二期（完成）

- OTA / vision / health / metrics → `xiaozhi-model-admin`
- 完整 WS 协议、连接上限、生产鉴权 → `xiaozhi-access`
- manage-api 配置拉取与热更新广播

### 第三期（完成）

- 迁 LLM / Intent / Memory / tools / plugins → `xiaozhi-agent`
- 插件改为 Session API；MCP/IoT 经 access 代理
- 默认 EchoLLM；OpenAI 兼容接口可配置

### 第四期（当前）

- 迁 TTS providers、分句队列、rate controller → `xiaozhi-audio-speaker`
- abort 清空 speaker；broadcast_speak 经 admin→speaker→access
- 默认 EchoTTS；EdgeTTS 可配置

### 第五期：VAD / ASR

- preprocess 承接 VAD；receiver 承接 ASR
- AEC 参考音通道；listen/detect 与 access 状态对齐

### 第六期：收口

- 韧性、指标、连接上限落地
- 生产 compose / 门禁切换
- **删除** `main/xiaozhi-server`，文档与 CI 指向本目录

## 关键耦合（后续必须显式设计）

1. 原 `ConnectionHandler` 神对象 → access 只留会话壳；agent 用 Session API
2. Abort/barge-in → `Agent.Abort` + `Speaker.Abort` 扇出（preprocess 待第五期）
3. AEC 耦合 speaker ↔ preprocess
4. 设备 MCP/IoT 同 WS → agent 经 access 代理（第三期已接通基础路径）
5. 插件不再依赖 Python `conn` 对象（第三期 Session 替代）

## 目录结构

```
xiaozhi-microserver/
  README.md
  requirements.txt
  start_dev_services.bat|.sh
  docker-compose.yml
  common/
    proto/  generate_proto.py  generated/xiaozhi/  xiaozhi_common/
  xiaozhi-access/
  xiaozhi-agent/          # Session / chat_engine / LLM / plugins
  xiaozhi-audio-preprocess/
  xiaozhi-audio-receiver/
  xiaozhi-audio-speaker/  # SpeakSession / EchoTTS / EdgeTTS / rate controller
  xiaozhi-model-admin/
  scripts/ws_smoke.py  scripts/agent_smoke.py  scripts/ota_smoke.py  scripts/run_tests.py
  tests/test_agent_phase3.py  tests/test_speaker_phase4.py
```
