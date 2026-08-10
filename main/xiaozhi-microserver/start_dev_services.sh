#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BASE=(--env=dev --group_name=DEV_GROUP --disable_nacos --nacos_host=127.0.0.1 --nacos_port=8848)

echo "[INFO] Starting xiaozhi-microserver..."

# Peers first so control-admin startup broadcast can reach them.
(
  cd "$ROOT/xiaozhi-access"
  python main.py "${BASE[@]}" --grpc_port 50051 --http_port 8000
) &
(
  cd "$ROOT/xiaozhi-audio-receiver"
  python main.py "${BASE[@]}" --grpc_port 50055
) &
(
  cd "$ROOT/xiaozhi-audio-speaker"
  python main.py "${BASE[@]}" --grpc_port 50053
) &
(
  cd "$ROOT/xiaozhi-agent"
  python main.py "${BASE[@]}" --grpc_port 50052
) &
(
  cd "$ROOT/xiaozhi-audio-preprocess"
  python main.py "${BASE[@]}" --grpc_port 50054
) &
echo "[INFO] Waiting for peer gRPC ports before control-admin..."
sleep 4
(
  cd "$ROOT/xiaozhi-control-admin"
  python main.py "${BASE[@]}" --grpc_port 50056 --http_port 8003
) &

echo "[SUCCESS] Services launched in background."
echo "[TIPS] WS: ws://127.0.0.1:8000/xiaozhi/v1/?device-id=test-001"
echo "[TIPS] OTA: http://127.0.0.1:8003/xiaozhi/ota/"
echo "[TIPS] If broadcast warned UNAVAILABLE: curl -X POST http://127.0.0.1:8003/config/reload"
echo "[TIPS] Smoke: python scripts/ws_smoke.py && python scripts/agent_smoke.py --mcp --iot --exit"
wait
