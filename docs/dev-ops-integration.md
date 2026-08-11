# 全模块源码部署自动升级方法

Python 语音侧请用 `start_dev_services.sh` / docker compose 启停**六服务**，不要再执行 `python app.py`。私有配置优先放在 `xiaozhi-control-admin/data/.config.yaml`。Java / 前端自动更新思路仍可用。

本教程面向全模块源码部署爱好者，通过脚本自动拉取、编译并重启服务。

测试平台示例：`https://2662r3426b.vicp.fun`。视频可参考：[《开源小智服务器自动更新…》](https://www.bilibili.com/video/BV15H37zHE7Q)（步骤中的单体入口请按本分支改写）。

# 开始条件
- Linux
- 已跑通本分支全模块流程
- 需要跟进代码并减少手工重启

# 教程效果
- 自动拉取代码并编译前端 / Java
- 自动重启 manager-api（8002）
- **重启六微服务**（勿再杀单一 `app.py`）

# 配置与模型路径

```
main/xiaozhi-microserver/xiaozhi-control-admin/data/.config.yaml
main/xiaozhi-microserver/models/SenseVoiceSmall/model.pt
```

# Python / 六服务更新示例（替换旧 update_8000.sh）

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /home/system/xiaozhi/xiaozhi-esp32-server
git pull origin Microservices_architecture

cd main/xiaozhi-microserver
source ~/.bashrc
conda activate xiaozhi-esp32-server   # 按你的环境名调整
pip install -r requirements.txt
python common/generate_proto.py

# 停旧进程后重启六服务（推荐 compose；或本仓库 start_dev_services.sh）
docker compose -f docker-compose.yml down || true
docker compose -f docker-compose.yml up -d --build
# 源码方式示例：
# bash start_dev_services.sh
```

manager-web / manager-api 的编译重启脚本可继续沿用上游教程结构，仅需把 `git pull origin main` 改为本分支名，并确认工作目录仍在本仓库。

# 注意事项
- 反向代理参考：[nginx 相关讨论](https://github.com/xinnan-tech/xiaozhi-esp32-server/issues/791)
- Liquibase 仍会自动迁移 manager-api 库表，一般无需手跑 SQL
- 更稳妥的生产发布用镜像 tag + [Production.md](./Production.md)，而不是服务器上直接 `git pull`
