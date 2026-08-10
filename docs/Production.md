# 生产部署

本分支已做生产级加固；按本文配置后可用于自托管生产。默认开发配置勿公网裸奔。

**主运行时**为 [`main/xiaozhi-microserver`](../main/xiaozhi-microserver)（六微服务）。  
智控台 `manager-api` / `manager-web` 继续保留，由 `xiaozhi-control-admin` 对接。

安装步骤见 [Deployment.md](./Deployment.md) / [Deployment_all.md](./Deployment_all.md)。  
主线各服务 `config.yaml` 仍为 **development**；生产用 compose 叠加层，不改主线默认。

---

## 0. 开箱生产（推荐 · microserver）

```bash
cd main/xiaozhi-microserver
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

| 文件 | 作用 |
|------|------|
| `docker-compose.yml` | 六服务本地/容器编排（无 Nacos，`--peer` 互发现） |
| `docker-compose.prod.yml` | 强制 `XIAOZHI_ENV=production`，挂载生产 overlay + healthcheck |
| `deploy/production/config.overlay.yaml` | 鉴权 / 连接上限 / 韧性 / 控制面门禁 |
| `config.production.example.yaml` | 生产配置片段参考 |

部署后把 overlay 里的 `websocket` / `vision_explain` 改成设备可达地址；设置 `server.auth_key` 或 `server.admin.token`。

源码开发启动：`start_dev_services.bat` / `bash start_dev_services.sh`，冒烟见 microserver README。

---

## 1. 必做

| # | 项 | 做法 |
|---|----|------|
| 1 | 环境 | 生产 compose 已强制；或 `XIAOZHI_ENV=production` / `server.environment: production` |
| 2 | 密钥 | `server.auth_key` / `server.admin.token` / `manager-api.secret` 用强随机值 |
| 3 | 设备认证 | production 默认强制 auth；勿设 `auth.allow_insecure_disable: true` |
| 4 | 控制面门禁 | production 下 `/config`、`/config/reload`、`/broadcast_speak` 需 `Authorization: Bearer <token>` 或 `X-Admin-Token` |
| 5 | 白名单 | `allowed_devices: []`；production 默认禁止免检 |
| 6 | 对外地址 | 配置真实 `websocket` / `vision_explain`（公网用 `wss`/`https`） |
| 7 | 探活 | 编排用 `GET /health`；负载均衡用 `GET /ready`（503=摘流） |
| 8 | 指标 | 抓取 access `GET :8000/metrics` 与 admin `GET :8003/metrics` |

参考：[`config.production.example.yaml`](../main/xiaozhi-microserver/config.production.example.yaml)

---

## 2. 探活

| 服务 | 路径 | 用途 | 成功 | 失败 |
|------|------|------|------|------|
| access | `:8000/health` | liveness | `200` `ok` | 进程无响应 |
| access | `:8000/ready` | readiness | `200` ready | `503` 连接打满 |
| control-admin | `:8003/health` | liveness | `200` `ok` | 进程无响应 |
| control-admin | `:8003/ready` | readiness | `200` ready | `503` access 打满 |

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/ready
curl -sS http://127.0.0.1:8003/health
curl -sS http://127.0.0.1:8003/ready
```

---

## 3. 上线自检

```text
[ ] environment = production
[ ] 无弱密码；admin token / auth_key 已配置
[ ] /health、/ready → 200；设备 auth.enabled=true
[ ] 无 token 访问 /config → 401；有 token 能读
[ ] 无 token 设备 WS 被拒；有 token 能连
[ ] /metrics 有 xiaozhi_ws_active_connections
[ ] 韧性：server.resilience.enabled=true
```

---

## 4. 容量（基线 · access）

| 维度 | 建议 |
|------|------|
| 挂机连接 | 默认硬上限 `max_connections=200`；`max_connections_per_device=2` |
| 上游 | ASR/LLM/TTS 经 gRPC 超时/重试/熔断（`server.resilience`） |
