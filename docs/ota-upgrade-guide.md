# 固件 OTA 自动升级（control-admin）

OTA 由 **`xiaozhi-control-admin`（:8003）** 提供。全模块智控台仍可管理固件元数据；下文为 control-admin 本地 `bin/` 方式。

## 前提

- 六微服务已按 [Deployment.md](./Deployment.md) / [Production.md](./Production.md) 跑通  
- 设备 OTA 地址指向 `http(s)://<host>:8003/xiaozhi/ota/`（或经反代的等价路径）

## 固件文件

将固件放到 control-admin 数据目录下的 `bin/`（若目录不存在请创建），命名：

```
{设备型号}_{版本号}.bin
```

示例：`lichuang-dev_1.6.6.bin`

常见数据根目录：

- 源码 / 开发：`main/xiaozhi-microserver/xiaozhi-control-admin/data/bin/`
- Docker：以 compose 挂载的 control-admin data 卷为准

## 配置提示

- 设备侧「OTA 地址」填 control-admin 可达 URL，不要再填已删除的单体 `:8003` 混用路径（本分支 OTA 即 admin）。  
- 生产环境请配置真实 `websocket` 列表（勿留空分号槽），见 [Production.md](./Production.md)。  
- 智控台全模块部署下，固件与参数也可经 manager-api / 智控台维护；以你实际部署为准。

## 验证

```bash
curl -sS http://127.0.0.1:8003/xiaozhi/ota/
# 或按设备协议 POST，确认返回中的 websocket / firmware 字段合理
```

更细的协议与烧录见 [firmware-setting.md](./firmware-setting.md)、[firmware-build.md](./firmware-build.md)。
