@echo off
chcp 65001 > nul
setlocal
set "ROOT=%~dp0"
set "BASE=--env=dev --group_name=DEV_GROUP --disable_nacos --nacos_host=127.0.0.1 --nacos_port=8848"

echo [INFO] Starting xiaozhi-microserver (phase-3 agent LLM/tools)...
echo [INFO] Nacos disabled by default; remove --disable_nacos when Nacos is available.
echo ========================================

start "xiaozhi-model-admin" cmd /k "cd /d "%ROOT%xiaozhi-model-admin" && python main.py %BASE% --grpc_port 50056 --http_port 8004"
start "xiaozhi-audio-receiver" cmd /k "cd /d "%ROOT%xiaozhi-audio-receiver" && python main.py %BASE% --grpc_port 50055"
start "xiaozhi-audio-speaker" cmd /k "cd /d "%ROOT%xiaozhi-audio-speaker" && python main.py %BASE% --grpc_port 50053"
start "xiaozhi-agent" cmd /k "cd /d "%ROOT%xiaozhi-agent" && python main.py %BASE% --grpc_port 50052"
start "xiaozhi-audio-preprocess" cmd /k "cd /d "%ROOT%xiaozhi-audio-preprocess" && python main.py %BASE% --grpc_port 50054"
timeout /t 2 /nobreak > nul
start "xiaozhi-access" cmd /k "cd /d "%ROOT%xiaozhi-access" && python main.py %BASE% --grpc_port 50051 --http_port 8103"

echo ========================================
echo [SUCCESS] Launch commands sent.
echo [TIPS] WS: ws://127.0.0.1:8103/xiaozhi/v1/?device-id=test-001
echo [TIPS] OTA: http://127.0.0.1:8004/xiaozhi/ota/
echo [TIPS] Smoke: python scripts\ws_smoke.py ^&^& python scripts\agent_smoke.py --mcp --iot --exit
echo [TIPS] All tests: python scripts\run_tests.py
echo.
pause
