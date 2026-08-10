# 本地编译 docker 镜像

若直接使用项目发行镜像且无需自编译，可忽略本文。

修改源码并以 docker 部署时，按下列步骤操作。

## 1、环境准备

```bash
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## 2、编译镜像

准备好 `你的用户名` 与 `新的版本号`（如 `1.2.3` 或日期 `20260810`）。

在仓库根目录：

```bash
cd 项目根目录

# 基础镜像（依赖变更时）
docker build -f Dockerfile-server-base -t ghcr.io/你的用户名/xiaozhi-esp32-server:server-base .

# microserver 共用应用镜像（六服务同一镜像）
docker build -f Dockerfile-server -t 你的用户名/xiaozhi-esp32-server:新的版本号 .

# 智控台 Web
docker build -f Dockerfile-web -t 你的用户名/xiaozhi-esp32-server-web:新的版本号 .
```

`Dockerfile-server` 打出的镜像包含整个 `main/xiaozhi-microserver`；各进程入口由 compose 的 `command` / `working_dir` 区分。

## 3、修改 compose 镜像名

```bash
cd main/xiaozhi-microserver
```

编辑 `docker-compose.yml`（或自建 overlay），将各服务 `image: python:3.12-slim` 改为你刚编译的镜像，并去掉「每次启动 pip install」的 command（改为直接 `python main.py ...`），例如：

```yaml
services:
  xiaozhi-access:
    image: 你的用户名/xiaozhi-esp32-server:新的版本号
    working_dir: /opt/xiaozhi-esp32-server/xiaozhi-access
    command: >
      python main.py --env=dev --disable_nacos --grpc_port 50051 --http_port 8000
      --peer xiaozhi-audio-preprocess-grpc-service=xiaozhi-audio-preprocess:50054
```

智控台 Web 仍用 `Dockerfile-web` 产物；全量栈见 [Deployment_all.md](./Deployment_all.md)。

## 4、重启服务

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

## 5、验证

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8003/health
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f xiaozhi-access
```
