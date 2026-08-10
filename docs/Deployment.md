# 部署文档（本分支 · xiaozhi-microserver）

![请参考-最简化架构图](../docs/images/deploy1.png)

> 生产部署（开箱 compose / 探活 / 容量）：见 [Production.md](./Production.md)  
> 智控台全量（manager-api + MySQL + Redis + Web）：见 [Deployment_all.md](./Deployment_all.md)

本分支语音主线为 **六微服务** [`main/xiaozhi-microserver`](../main/xiaozhi-microserver)。  
设备侧：WebSocket `ws://host:8000/xiaozhi/v1/`，OTA `http://host:8003/xiaozhi/ota/`。

---

# 方式一：Docker 运行六服务

`0.8.2` 版本开始，发行的 docker 镜像默认面向 `x86`；`arm64` 可按 [docker-build.md](docker-build.md) 本机编译。

## 1. 安装 Docker

未安装可参考：[docker 安装](https://www.runoob.com/docker/ubuntu-docker-install.html)

## 2. 准备目录与模型

建议工作目录示例：`xiaozhi-microserver`（可直接克隆仓库后使用 `main/xiaozhi-microserver`）。

```
xiaozhi-microserver
  ├─ docker-compose.yml
  ├─ docker-compose.prod.yml
  ├─ models
  │    └─ SenseVoiceSmall
  │         └─ model.pt          # 需自行下载，见下文「模型文件」
  ├─ xiaozhi-control-admin/data
  │    └─ .config.yaml           # 可选私有覆盖
  └─ ...
```

仓库已自带 `models/snakers4_silero-vad` 与 SenseVoice 配置/分词器；**ASR 权重 `model.pt` 需下载**。

## 3. 配置

复制并编辑生产环境变量与 overlay：

```bash
cd main/xiaozhi-microserver
cp .env.example .env
# 编辑 deploy/production/config.overlay.yaml：websocket / vision_explain / token
```

各服务默认 `config.yaml` 与智控台对接说明见 microserver README。LLM 等密钥可写在各服务 config，或经 control-admin 从 manager-api 拉取。

## 4. 启动

```bash
cd main/xiaozhi-microserver
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

查看 access / admin 健康：

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8003/health
```

容器日志示例：

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f xiaozhi-access
```

成功标志：access `:8000`、control-admin `:8003` 探活 200；设备可用 `ws://<局域网IP>:8000/xiaozhi/v1/`。

## 5. 版本升级

1. 备份各服务私有配置与 `deploy/production/config.overlay.yaml`、`.env`  
2. 拉取新镜像 / 新代码后重新 `docker compose ... up -d`  
3. 逐项核对密钥，勿整文件盲目覆盖

---

# 方式二：本地源码运行六服务

## 1. 基础环境

推荐 conda（Windows 可用 Anaconda Prompt）：

```bash
conda create -n xiaozhi-esp32-server python=3.12 -y
conda activate xiaozhi-esp32-server
conda install libopus ffmpeg -y
```

Linux 若缺 `libiconv.so.2`：`conda install libiconv -y`。

## 2. 安装依赖

```bash
cd main/xiaozhi-microserver
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
pip install -r requirements.txt
# 本地 FunASR 时另装：
# pip install -r requirements-funasr.txt
python common/generate_proto.py
```

## 3. 模型文件

见下文「模型文件」；将 `model.pt` 放到 `main/xiaozhi-microserver/models/SenseVoiceSmall/`。

## 4. 配置

按 README「切换真实模型」编辑各服务 `config.yaml`（至少 LLM `api_key`）。  
对接智控台时配置 `xiaozhi-control-admin` 的 manager-api 地址与 secret。

## 5. 启动

```bash
# Windows
start_dev_services.bat

# Linux / macOS
bash start_dev_services.sh
```

冒烟：

```bash
python scripts/ws_smoke.py
python scripts/ota_smoke.py
```

---

# 汇总

## 配置项目

- 控制面私有配置：`xiaozhi-control-admin/data/.config.yaml`（可参考 `config_from_api.yaml`）  
- 各业务服务：目录内 `config.yaml`；生产叠加见 `deploy/production/config.overlay.yaml`  
- 默认 LLM 示例常用 ChatGLM / Doubao，需在官网申请密钥

最简 agent 侧密钥示例（`xiaozhi-agent/config.yaml`）：

```yaml
selected_module:
  LLM: ChatGLM
LLM:
  ChatGLM:
    type: openai
    model_name: glm-4-flash
    url: https://open.bigmodel.cn/api/paas/v4/
    api_key: 你的key
```

设备可达地址写入 control-admin / overlay 的 `server.websocket`（或等价字段），例如：

```text
ws://192.168.1.25:8000/xiaozhi/v1/
http://192.168.1.25:8003/xiaozhi/ota/
```

## 模型文件

默认本地 ASR 使用 `SenseVoiceSmall`。权重较大，需独立下载，放到：

`main/xiaozhi-microserver/models/SenseVoiceSmall/model.pt`

- 线路一：阿里魔搭 [SenseVoiceSmall](https://modelscope.cn/models/iic/SenseVoiceSmall/resolve/master/model.pt)
- 线路二：百度网盘 [SenseVoiceSmall](https://pan.baidu.com/share/init?surl=QlgM58FHhYv1tFnUT_A8Sg&pwd=qvna) 提取码 `qvna`

VAD 使用仓库内 `models/snakers4_silero-vad`（ONNX）。

## 运行状态确认

- Access：`http://127.0.0.1:8000/health`、`/ready`  
- control-admin：`http://127.0.0.1:8003/health`；OTA `http://<IP>:8003/xiaozhi/ota/`  
- WebSocket：`ws://<局域网IP>:8000/xiaozhi/v1/`（勿用浏览器直接打开）

随后可编译 ESP32 固件或配置已有固件：

1. [编译自己的 esp32 固件](firmware-build.md)  
2. [基于已编译固件配置自定义服务器](firmware-setting.md)

# 常见问题与更多教程

见 [FAQ.md](./FAQ.md)。上线加固见 [Production.md](./Production.md)。
