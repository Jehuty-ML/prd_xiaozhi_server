<p align="center">
  <a href="https://github.com/Jehuty-ML/prd_xiaozhi_server">
    <img src="docs/images/banner1.png" alt="xiaozhi-esp32-server" width="100%"/>
  </a>
</p>

<h1 align="center">xiaozhi-esp32-server · Production Hardening Fork</h1>

<p align="center">
  <strong>Self-hosted backend for ESP32 “Xiaozhi” hardware</strong><br/>
  Devices speak; this repo runs the full <strong>listen → think → speak</strong> pipeline, plus a management console.<br/>
  Same feature baseline as upstream, plus connection limits, observability, safer defaults, and dependency resilience for real self-hosting.
</p>

<p align="center">
  <a href="./README.md"><img alt="简体中文" src="https://img.shields.io/badge/简体中文-DFE0E5"/></a>
  <a href="./README_en.md"><img alt="English" src="https://img.shields.io/badge/English-DBEDFA"/></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-Voice_Gateway-3776AB?logo=python&logoColor=white"/>
  <img alt="Java" src="https://img.shields.io/badge/Java-Manager_API-ED8B00?logo=openjdk&logoColor=white"/>
  <img alt="Vue" src="https://img.shields.io/badge/Vue-Manager_Web-4FC08D?logo=vue.js&logoColor=white"/>
  <img alt="License" src="https://img.shields.io/badge/License-MIT-blue"/>
</p>

---

## 30-second Quick Start

> Default config is **development** — do **not** expose it to the public internet. Production checklist: [Production.md](./docs/Production.md).

```bash
git clone https://github.com/Jehuty-ML/prd_xiaozhi_server.git
cd prd_xiaozhi_server/main/xiaozhi-server
cp .env.example .env   # change MYSQL_ROOT_PASSWORD and other secrets

# Voice server only (fastest path)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

curl -sS http://127.0.0.1:8003/health
```

Full stack (console + MySQL + Redis): `docker-compose_all.yml` + `docker-compose_all.prod.yml`.

---

## What this is

<p align="center">
  <img src="docs/images/overview.svg" alt="System overview" width="100%"/>
</p>

| You see | What it actually is |
|---------|---------------------|
| An ESP32 gadget that chats / controls IoT | Firmware: [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) |
| The “brain”: ASR, LLM, TTS | **This repo** — `xiaozhi-server` |
| Browser UI for devices, roles, models, OTA | `manager-web` + `manager-api` |
| Difference from official `xiaozhi.me` | Keys & data stay on **your** servers |

**vs upstream in one line:** feature parity with [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server); this fork turns “demo-ready” into “self-host production-ready”.

---

## This fork vs upstream

<p align="center">
  <img src="docs/images/vs-upstream.svg" alt="Fork vs upstream" width="100%"/>
</p>

| Area | Upstream baseline | This fork |
|------|-------------------|-----------|
| Positioning | Demo & DIY | Self-hosted **production surface** |
| Connections | Mostly unlimited | Global / per-device hard caps |
| Observability | Logs-first | `/health` `/ready` `/metrics` |
| Security | Dev-friendly defaults | `production` forces auth |
| Upstream failures | Often leak to devices | Timeout / retry / circuit break / fallback |

---

## Management console

<p align="center">
  <img src="docs/images/console-devices.png" alt="Device management" width="48%"/>
  <img src="docs/images/console-models.png" alt="Model config" width="48%"/>
</p>

---

## Demo glance

<p align="center">
  <img src="docs/images/demo-flow.gif" alt="Listen-think-speak flow" width="80%"/>
</p>

<p align="center">
  <img src="docs/images/demo2.png" alt="Custom voice" width="32%"/>
  <img src="docs/images/demo3.png" alt="Cantonese" width="32%"/>
  <img src="docs/images/demo0.png" alt="Inter-device call" width="32%"/>
</p>

Full demo videos: [upstream README](https://github.com/xinnan-tech/xiaozhi-esp32-server).

---

## Architecture choice (read first)

| Branch | Runtime | Best for |
|--------|---------|----------|
| **`main` (monolith)** | `main/xiaozhi-server` | Most teams starting out |
| [`Microservices_architecture`](https://github.com/Jehuty-ML/prd_xiaozhi_server/tree/Microservices_architecture) | `main/xiaozhi-microserver` | High concurrency / per-stage scaling |

Prefer **`main`** unless you clearly need segmented scaling.

---

## Docs

| Doc | Purpose |
|-----|---------|
| [README (中文)](./README.md) | Full Chinese guide (source of truth for ops detail) |
| [Production.md](./docs/Production.md) | Go-live checklist |
| [Deployment.md](./docs/Deployment.md) / [Deployment_all.md](./docs/Deployment_all.md) | Install steps |
| [github-repo-meta.md](./docs/github-repo-meta.md) | Description / Topics / Social Preview |
| [social-preview.png](./docs/images/social-preview.png) | 1280×640 OG image |

---

## Warning

1. No commercial relationship with third-party ASR / LLM / TTS vendors; keep your own API keys safe.  
2. Do not open to the public internet before following [Production.md](./docs/Production.md).

## License & links

- License: MIT ([LICENSE](./LICENSE))  
- Upstream: [xinnan-tech/xiaozhi-esp32-server](https://github.com/xinnan-tech/xiaozhi-esp32-server)  
- Firmware: [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32)  
- This repo: https://github.com/Jehuty-ML/prd_xiaozhi_server
