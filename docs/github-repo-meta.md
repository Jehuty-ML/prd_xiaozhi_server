# GitHub 仓库元信息（需本机已登录 gh）

当前环境未登录 GitHub CLI。在仓库根目录执行下面命令即可设置 Description 与 Topics：

```bash
gh repo edit Jehuty-ML/prd_xiaozhi_server \
  --description "小智 ESP32 自托管后端 · 生产加固（连接治理 /metrics / 安全默认 / 熔断降级）" \
  --add-topic esp32 \
  --add-topic xiaozhi \
  --add-topic websocket \
  --add-topic voice-assistant \
  --add-topic mqtt \
  --add-topic self-hosted \
  --add-topic production

# Social Preview：仓库 Settings → General → Social preview
# 上传 docs/images/social-preview.png（1280×640）
```

若尚未登录：

```bash
gh auth login
```
