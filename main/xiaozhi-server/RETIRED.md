# xiaozhi-server 已退役（第六期收口）

> **本目录不再作为主运行时。** CI 门禁与生产文档已切换到
> [`../xiaozhi-microserver`](../xiaozhi-microserver)。

## 为何保留

单体仍含完整 ASR/TTS/LLM provider、FunASR 模型路径与历史 compose，供对照与迁移。
新部署请使用 microserver；勿再向本目录提交功能。

## 迁移

| 原路径 | 新路径 |
|--------|--------|
| `main/xiaozhi-server` | `main/xiaozhi-microserver`（六进程） |
| `docker-compose*.yml`（本目录） | `xiaozhi-microserver/docker-compose.yml` + `docker-compose.prod.yml` |
| 单元测试 CI | `.github/workflows/xiaozhi-microserver-tests.yml` |
| 生产门禁 | [`docs/Production.md`](../../docs/Production.md) → microserver 章节 |

物理删除本树可在 provider 能力对齐后单独执行；当前刻意保留以免打断存量镜像构建（`Dockerfile-server`）。
