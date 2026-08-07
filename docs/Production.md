# 生产部署

本分支已做生产级加固；按本文配置后可用于自托管生产；默认开发配置勿公网暴露。

安装步骤见 [Deployment.md](./Deployment.md)（仅 Server）/ [Deployment_all.md](./Deployment_all.md)（全模块）。  
本仓库是双进程：`manager-api`（智控台）+ `xiaozhi-server`（对话网关）。

---

## 1. 必做

| # | 项 | 做法 |
|---|----|------|
| 1 | 环境 | `XIAOZHI_ENV=production`，或 `server.environment: production` |
| 2 | 密钥 | MySQL / Redis / `manager-api.secret` / `server.auth_key` 用强随机值，勿用 `123456` |
| 3 | 认证 | production 默认强制 auth；勿设 `auth.allow_insecure_disable: true` |
| 4 | 白名单 | `allowed_devices: []`；production 默认禁止免检（`allow_whitelist_bypass: auto/false`） |
| 5 | 对外地址 | 配置真实 `websocket` / `vision_explain`（公网用 `wss`/`https`） |
| 6 | 探活 | 编排用 `GET /health`；负载均衡用 `GET /ready`（503=摘流） |
| 7 | 指标 | 抓取 `GET /metrics` |

参考：[`config.production.example.yaml`](../main/xiaozhi-server/config.production.example.yaml)

```bash
cd main/xiaozhi-server
cp .env.example .env   # 必改 MYSQL_ROOT_PASSWORD；确认 XIAOZHI_ENV=production
docker compose up -d                                    # 仅 Server
docker compose -f docker-compose_all.yml up -d          # 全模块
```

未设置 `MYSQL_ROOT_PASSWORD` 时全模块 compose 会失败。

---

## 2. 探活

| 路径 | 用途 | 成功 | 失败 |
|------|------|------|------|
| `/health` | liveness | `200` `{"status":"ok"}` | 进程无响应 |
| `/ready` | readiness | `200` `ready` | `503`：未就绪 / 连接打满 / registry Redis 不通 |

```bash
curl -sS http://127.0.0.1:8003/health
curl -sS http://127.0.0.1:8003/ready
```

---

## 3. 上线自检

```text
[ ] environment = production
[ ] 无弱密码
[ ] /health、/ready → 200，auth.enabled=true
[ ] 无 token 被拒；有 token 能连
[ ] /metrics 有 xiaozhi_ws_active_connections
```

---

## 4. 容量（1 核基线）

| 维度 | 建议 |
|------|------|
| 同时开口 | ≤ **80**（=`max_concurrent_chats`） |
| 挂机连接 | 默认硬上限 `max_connections=200`；压测可达 ~2000（约 0.5–1 MB/连接，需调高上限） |

复跑（先 `python app.py`）：

```bash
python scripts/capacity_bench.py --mode idle --pin-cores 1 --auto-find-server \
  --idle-target 2000 --report-json data/bench-idle.json
python scripts/capacity_bench.py --mode chat --pin-cores 1 --auto-find-server \
  --concurrency 80 --report-json data/bench-chat-80.json
```

挂机压测需临时加大 `close_connection_no_voice_time`（否则约 180s 踢线）。智控台模式先 `seed_bench_devices.py`。
