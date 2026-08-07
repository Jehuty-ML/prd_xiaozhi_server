#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
BASE=(--env=dev --group_name=DEV_GROUP --disable_nacos --nacos_host=127.0.0.1 --nacos_port=8848)

echo "[INFO] Starting xiaozhi-microserver (phase-3 agent LLM/tools)..."

(
  cd "$ROOT/xiaozhi-model-admin"
  python main.py "${BASE[@]}" --grpc_port 50056 --http_port 8004
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
sleep 2
(
  cd "$ROOT/xiaozhi-access"
  python main.py "${BASE[@]}" --grpc_port 50051 --http_port 8103
) &

echo "[SUCCESS] Services launched in background."
echo "[TIPS] WS: ws://127.0.0.1:8103/xiaozhi/v1/?device-id=test-001"
echo "[TIPS] OTA: http://127.0.0.1:8004/xiaozhi/ota/"
echo "[TIPS] Smoke: python scripts/ws_smoke.py && python scripts/agent_smoke.py --mcp --iot --exit"
echo "[TIPS] All tests: python scripts/run_tests.py"
wait
