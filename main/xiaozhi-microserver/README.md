# xiaozhi-microserver

将开源单体 [`xiaozhi-server`](../xiaozhi-server) 按 Nacos + gRPC 微服务架构拆分后的实现（第一期：骨架 + 契约 + 假链路联调）。

> 参考架构：仓库旁 `micro_service` 的进程划分与通信方式。  
> 命名：全部使用 **xiaozhi** 语义，不引入专有业务名词。

## 六个微服务

| 目录 | Nacos 名 | Dev 端口 | 职责 |
|------|----------|----------|------|
| `xiaozhi-access` | `xiaozhi-access-grpc-service` | HTTP **8103** / gRPC **50051** | 设备 WebSocket 入口、鉴权钩子、协议解复用、会话状态、下行 TTS/指令回写；上行转 preprocess |
| `xiaozhi-agent` | `xiaozhi-agent-grpc-service` | gRPC **50052** | Intent / LLM / Memory / 工具插件 / MCP·IoT（经 access 代理）；句子流给 speaker |
| `xiaozhi-audio-speaker` | `xiaozhi-audio-speaker-grpc-service` | gRPC **50053** | TTS 合成与分句队列；经 access `SendTtsAudio` 写回设备 |
| `xiaozhi-audio-preprocess` | `xiaozhi-audio-preprocess-grpc-service` | gRPC **50054** | 解码 / VAD 分句；调 receiver ASR；通知 access 听状态；投递文本给 agent |
| `xiaozhi-audio-receiver` | `xiaozhi-audio-receiver-grpc-service` | gRPC **50055** | ASR：PCM → 文本（含声纹扩展位） |
| `xiaozhi-model-admin` | `xiaozhi-model-admin-grpc-service` | HTTP **8004** / gRPC **50056** | 配置 / 热更新 / manager-api 对接 / OTA·视觉·health·metrics（控制面） |

公共库：`common/xiaozhi_common`（配置、Nacos、gRPC、session DTO）+ `common/proto`（统一契约）。

> 并存说明：原单体常用 HTTP `8003`，本仓库 access 默认用 **8103**，避免与仍在运行的 `xiaozhi-server` 冲突。删除单体后可改回 8003。

## 假链路（第一期）

```
Device --WS--> access --gRPC--> preprocess --gRPC--> receiver (ASR stub)
                              |--gRPC--> agent (echo reply)
                              |--gRPC--> speaker (TTS stub bytes)
                              |--gRPC--> access --WS--> Device
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
```

默认带 `--disable_nacos`，用静态端口互发现。有 Nacos 时去掉该参数，并配置 `--nacos_host` / `--group_name` / `--env_id`。

可用 `--peer name=host:port` 覆盖发现地址。

### 手动探活

- Access health: `http://127.0.0.1:8103/health`
- Model-admin health: `http://127.0.0.1:8004/health`
- Model-admin config: `http://127.0.0.1:8004/config`
- WS: `ws://127.0.0.1:8103/ws?device-id=test-001`  
  发送：`{"type":"listen","text":"hello"}`，应收到 `type=tts` JSON 与 stub 二进制。

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
| `common/proto/audio.proto` | `SendAudioChunk` / `AsrRecognize` / `SendText` / `SpeakText` / `SendTtsAudio` |
| `common/proto/command.proto` | listen 状态、设备指令 |
| `common/proto/session.proto` | Abort 扇出（二期 barge-in） |
| `common/proto/admin.proto` | GetConfig / ReloadConfig / Health |

## 演进路线

### 第一期（当前）

骨架、统一 proto、Nacos/静态发现、假 ASR/LLM/TTS 端到端联调。  
**不删除** `xiaozhi-server`，两边并存。

### 第二期：控制面 + 网关真实化

- OTA / vision / health / metrics → `xiaozhi-model-admin`
- 完整 WS 协议、连接上限、生产鉴权 → `xiaozhi-access`
- manage-api 配置拉取与热更新广播

### 第三期：Agent

- 迁 LLM / Intent / Memory / tools / plugins
- 插件改为 Session API；MCP/IoT 经 access 代理

### 第四期：TTS

- 迁 TTS providers、分句队列、rate controller
- abort 清空 speaker；broadcast_speak 经 admin→speaker→access

### 第五期：VAD / ASR

- preprocess 承接 VAD；receiver 承接 ASR
- AEC 参考音通道；listen/detect 与 access 状态对齐

### 第六期：收口

- 韧性、指标、连接上限落地
- 生产 compose / 门禁对齐
- **删除** `main/xiaozhi-server`，文档与 CI 指向本目录

## 关键耦合（后续必须显式设计）

1. 原 `ConnectionHandler` 神对象 → access 只留会话壳
2. Abort/barge-in → `session.Abort` 扇出
3. AEC 耦合 speaker ↔ preprocess
4. 设备 MCP/IoT 同 WS → agent 经 access 代理
5. 插件不再依赖 Python `conn` 对象

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
  xiaozhi-agent/
  xiaozhi-audio-preprocess/
  xiaozhi-audio-receiver/
  xiaozhi-audio-speaker/
  xiaozhi-model-admin/
  scripts/ws_smoke.py
```
