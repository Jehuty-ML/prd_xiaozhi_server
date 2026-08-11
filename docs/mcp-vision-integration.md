# 视觉接口（control-admin）

本分支视觉 HTTP 入口在 **`xiaozhi-control-admin`（默认 `:8003`）**，路径：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/mcp/vision/explain` | 探活文案 |
| POST | `/mcp/vision/explain` | 上传图片 + 问题（需鉴权） |

默认配置见 `xiaozhi-control-admin/config.yaml`：

```yaml
server:
  vision_explain: "http://127.0.0.1:8003/mcp/vision/explain"
```

OTA 应答里也会带上该地址（或按配置改写），供固件侧摄像头识物调用。

## 当前能力边界

- **鉴权、CORS、图片校验已实现**（见 `xiaozhi-control-admin/app/api/vision.py`）。
- **VLLM 推理仍为 stub**：即使配置了 `selected_module.VLLM`，POST 也只返回占位说明，不会真正调用视觉大模型。完整识物需等 agent / VLLM Provider 接入后再改本文。

## 联调步骤

1. 六服务已启动，确认：

```bash
curl -sS http://127.0.0.1:8003/mcp/vision/explain
# Vision interface is running (xiaozhi-control-admin)
```

2. 设备或公网访问时，把 `server.vision_explain`（及智控台等价参数）改成**设备可达**的 URL，不要写仅本机可访问的 `127.0.0.1`。
3. 生产环境勿依赖 `web_test_client` 免鉴权；需合法 Vision JWT（与 access / 设备侧一致）。
4. 固件需支持摄像头与识物协议（如固件 ≥ 1.6.6，且板型已实现拍照）。

## 与上游文档的差异

- 不再有单体 `xiaozhi-server` 的 `python app.py` / 同进程 8003。
- 勿再按「改 `data/.config.yaml` 后重启单个 `xiaozhi-esp32-server` 容器」操作；改 **control-admin** 配置并重启该服务。
