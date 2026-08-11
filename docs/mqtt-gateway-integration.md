# MQTT 网关接入（本分支）

设备可通过外部 [xiaozhi-mqtt-gateway](https://github.com/78/xiaozhi-mqtt-gateway)（或社区改造版）走 MQTT+UDP，再反向连到本分支的 **`xiaozhi-access` WebSocket**。

本仓库 **不内置** MQTT broker；access 侧也未单独解析 `from=mqtt_gateway` 业务逻辑——网关只要能连上 access 的 `/xiaozhi/v1/` 即可。OTA 侧可下发 `mqtt` 字段（`xiaozhi-control-admin` 读取 `server.mqtt_gateway` / `mqtt_signature_key` 等）。

## 准备

1. 六服务已按 [Deployment.md](./Deployment.md) 跑通；设备 WebSocket 入口为 **access `:8000`**：

```text
ws://<access可达地址>:8000/xiaozhi/v1/
```

网关配置里的 `chat_servers` 填上述地址（可带 `?from=mqtt_gateway` 便于自查日志，非必须）。

2. 若希望 OTA 返回 MQTT 接入信息，在 **control-admin** 配置（或智控台参数，经配置同步）填写例如：

```yaml
server:
  mqtt_gateway: "<mqtt网关对外 host:port>"
  mqtt_signature_key: "<与网关约定的签名密钥>"
```

具体字段以 `xiaozhi-control-admin` 的 OTA 实现与网关文档为准。

## 部署网关（摘要）

1. 克隆并安装网关（示例用社区改造仓，也可跟上游固件文档）：

```bash
git clone https://github.com/xinnan-tech/xiaozhi-mqtt-gateway.git
cd xiaozhi-mqtt-gateway
npm install
```

2. 将 `chat_servers` 指向本分支 **access**，不要再写已删除的单体 `xiaozhi-server`。
3. 公网需放行网关文档要求的端口（常见 `1883` / `8884(UDP)` / `8007` 等，以网关版本为准）。
4. 设备侧按网关 / 固件说明改用 MQTT 配网；OTA 仍指向 **control-admin `:8003`**。

## 注意

- 全模块部署时，智控台参数与 control-admin 私有配置需一致，避免 OTA 下发的 websocket / mqtt 与真实 access、网关不符。
- 更细的网关安装步骤以网关仓库 README 为准；本文只固定本分支「连到 access / OTA 在 admin」这一映射。
