# 天气插件（`get_weather`）

本分支天气能力在 **`xiaozhi-agent`** 插件中实现，调用 [Open-Meteo](https://open-meteo.com/)（地理编码 + 预报），**不需要 API Key**。

实现：`main/xiaozhi-microserver/xiaozhi-agent/app/plugins/functions/get_weather.py`

## 启用

编辑 `main/xiaozhi-microserver/xiaozhi-agent/config.yaml`（或由智控台下发到 agent 的同等配置）：

1. `selected_module.Intent` 使用 `function_call`（或其它会走到函数调用的意图模式）。
2. 在 `Intent.function_call.functions` 中包含 `get_weather`。
3. 可选：`plugins.get_weather` 块可保留作扩展；当前实现以 Open-Meteo 为准，未读和风天气密钥。

示例（与仓库默认接近）：

```yaml
selected_module:
  Intent: function_call

Intent:
  function_call:
    type: function_call
    functions:
      - get_time
      - get_weather

plugins:
  get_weather:
    api_host: "https://api.open-meteo.com"
```

改配置后重启 **xiaozhi-agent**（源码 `start_dev_services` / docker compose 重建该服务即可）。

## 使用

对设备说「杭州天气」「北京今天天气」等。未指定地点时，插件默认按「杭州」查询。

## 说明

- 上游单体文档里的**和风天气 API Key / Host** 流程不适用于本分支当前实现。
- 若对接智控台「天气插件」参数页，请确认 manager-api 下发字段与 agent 实际读取一致；以 agent 源码为准。
