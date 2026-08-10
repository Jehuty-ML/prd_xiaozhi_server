# 鐢熶骇閮ㄧ讲

鏈垎鏀凡鍋氱敓浜х骇鍔犲浐锛涙寜鏈枃閰嶇疆鍚庡彲鐢ㄤ簬鑷墭绠＄敓浜э紱榛樿寮€鍙戦厤缃嬁鍏綉鏆撮湶銆?

**绗叚鏈熻捣涓昏繍琛屾椂涓?* [`main/xiaozhi-microserver`](../main/xiaozhi-microserver)锛堝叚寰湇鍔★級銆? 
鍘嗗彶鍗曚綋 [`main/xiaozhi-server`](../main/xiaozhi-server) 宸查€€褰癸紙瑙佸叾 `RETIRED.md`锛夛紱瀛橀噺 Docker `server_*` 闀滃儚浠嶅彲鏋勫缓锛屾柊閮ㄧ讲璇疯蛋 microserver銆?

鏅烘帶鍙?`manager-api` / `manager-web` 缁х画淇濈暀锛岀敱 `xiaozhi-control-admin` 瀵规帴銆?

瀹夎姝ラ瑙?[Deployment.md](./Deployment.md) / [Deployment_all.md](./Deployment_all.md)锛堟枃妗ｄ腑鐨?Server 璺緞閫愭鍒囧埌 microserver锛夈€? 
涓荤嚎 `config.yaml` 浠嶄负 **development**锛涚敓浜х敤 compose 鍙犲姞灞傦紝涓嶆敼涓荤嚎榛樿銆?

---

## 0. 寮€绠辩敓浜э紙鎺ㄨ崘 路 microserver锛?

```bash
cd main/xiaozhi-microserver
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

| 鏂囦欢 | 浣滅敤 |
|------|------|
| `docker-compose.yml` | 鍏湇鍔℃湰鍦?瀹瑰櫒缂栨帓锛堟棤 Nacos锛宍--peer` 浜掑彂鐜帮級 |
| `docker-compose.prod.yml` | 寮哄埗 `XIAOZHI_ENV=production`锛屾寕杞界敓浜?overlay + healthcheck |
| `deploy/production/config.overlay.yaml` | 閴存潈 / 杩炴帴涓婇檺 / 闊ф€?/ 鎺у埗闈㈤棬绂?|
| `config.production.example.yaml` | 鐢熶骇閰嶇疆鐗囨鍙傝€?|

閮ㄧ讲鍚庢妸 overlay 閲岀殑 `websocket` / `vision_explain` 鏀规垚璁惧鍙揪鍦板潃锛涜缃?`server.auth_key` 鎴?`server.admin.token`銆?

婧愮爜寮€鍙戝惎鍔細`start_dev_services.bat` / `bash start_dev_services.sh`锛屽啋鐑熻 microserver README銆?

---

## 1. 蹇呭仛

| # | 椤?| 鍋氭硶 |
|---|----|------|
| 1 | 鐜 | 鐢熶骇 compose 宸插己鍒讹紱鎴?`XIAOZHI_ENV=production` / `server.environment: production` |
| 2 | 瀵嗛挜 | `server.auth_key` / `server.admin.token` / `manager-api.secret` 鐢ㄥ己闅忔満鍊?|
| 3 | 璁惧璁よ瘉 | production 榛樿寮哄埗 auth锛涘嬁璁?`auth.allow_insecure_disable: true` |
| 4 | 鎺у埗闈㈤棬绂?| production 涓?`/config`銆乣/config/reload`銆乣/broadcast_speak` 闇€ `Authorization: Bearer <token>` 鎴?`X-Admin-Token` |
| 5 | 鐧藉悕鍗?| `allowed_devices: []`锛沺roduction 榛樿绂佹鍏嶆 |
| 6 | 瀵瑰鍦板潃 | 閰嶇疆鐪熷疄 `websocket` / `vision_explain`锛堝叕缃戠敤 `wss`/`https`锛?|
| 7 | 鎺㈡椿 | 缂栨帓鐢?`GET /health`锛涜礋杞藉潎琛＄敤 `GET /ready`锛?03=鎽樻祦锛?|
| 8 | 鎸囨爣 | 鎶撳彇 access `GET :8000/metrics` 涓?admin `GET :8003/metrics` |

鍙傝€冿細[`config.production.example.yaml`](../main/xiaozhi-microserver/config.production.example.yaml)

---

## 2. 鎺㈡椿

| 鏈嶅姟 | 璺緞 | 鐢ㄩ€?| 鎴愬姛 | 澶辫触 |
|------|------|------|------|------|
| access | `:8000/health` | liveness | `200` `ok` | 杩涚▼鏃犲搷搴?|
| access | `:8000/ready` | readiness | `200` ready | `503` 杩炴帴鎵撴弧 |
| control-admin | `:8003/health` | liveness | `200` `ok` | 杩涚▼鏃犲搷搴?|
| control-admin | `:8003/ready` | readiness | `200` ready | `503` access 鎵撴弧 |

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/ready
curl -sS http://127.0.0.1:8003/health
curl -sS http://127.0.0.1:8003/ready
```

---

## 3. 涓婄嚎鑷

```text
[ ] environment = production
[ ] 鏃犲急瀵嗙爜锛沘dmin token / auth_key 宸查厤缃?
[ ] /health銆?ready 鈫?200锛岃澶?auth.enabled=true
[ ] 鏃?token 璁块棶 /config 鈫?401锛涙湁 token 鑳借
[ ] 鏃?token 璁惧 WS 琚嫆锛涙湁 token 鑳借繛
[ ] /metrics 鏈?xiaozhi_ws_active_connections
[ ] 闊ф€э細server.resilience.enabled=true
```

---

## 4. 瀹归噺锛? 鏍稿熀绾?路 access锛?

| 缁村害 | 寤鸿 |
|------|------|
| 鎸傛満杩炴帴 | 榛樿纭笂闄?`max_connections=200`锛沗max_connections_per_device=2` |
| 涓婃父 | ASR/LLM/TTS 缁?gRPC 瓒呮椂/閲嶈瘯/鐔旀柇锛坄server.resilience`锛?|

---

## 5. 鍘嗗彶鍗曚綋锛堝吋瀹癸級

鏃ц矾寰?`cd main/xiaozhi-server && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d` 浠嶅彲鐢ㄤ簬瀛橀噺鐜锛屼絾**涓嶅啀浣滀负鏂囨。涓?CI 涓荤嚎**銆?
