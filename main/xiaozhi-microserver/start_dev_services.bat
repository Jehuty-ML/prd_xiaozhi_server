@echo off
chcp 65001 > nul
setlocal
set "ROOT=%~dp0"
set "BASE=--env=dev --group_name=DEV_GROUP --disable_nacos --nacos_host=127.0.0.1 --nacos_port=8848"

echo [INFO] Starting xiaozhi-microserver...
echo [INFO] Nacos disabled by default; remove --disable_nacos when Nacos is available.
echo ========================================

REM Peers first so control-admin startup broadcast can reach them.
start "xiaozhi-access" cmd /k "cd /d "%ROOT%xiaozhi-access" && python main.py %BASE% --grpc_port 50051 --http_port 8000"
start "xiaozhi-audio-receiver" cmd /k "cd /d "%ROOT%xiaozhi-audio-receiver" && python main.py %BASE% --grpc_port 50055"
start "xiaozhi-audio-speaker" cmd /k "cd /d "%ROOT%xiaozhi-audio-speaker" && python main.py %BASE% --grpc_port 50053"
start "xiaozhi-agent" cmd /k "cd /d "%ROOT%xiaozhi-agent" && python main.py %BASE% --grpc_port 50052"
start "xiaozhi-audio-preprocess" cmd /k "cd /d "%ROOT%xiaozhi-audio-preprocess" && python main.py %BASE% --grpc_port 50054"
echo [INFO] Waiting for peer gRPC ports before control-admin...
timeout /t 4 /nobreak > nul
start "xiaozhi-control-admin" cmd /k "cd /d "%ROOT%xiaozhi-control-admin" && python main.py %BASE% --grpc_port 50056 --http_port 8003"

echo ========================================
echo [SUCCESS] Launch commands sent.
echo [TIPS] WS: ws://127.0.0.1:8000/xiaozhi/v1/?device-id=test-001
echo [TIPS] OTA: http://127.0.0.1:8003/xiaozhi/ota/
echo [TIPS] If broadcast warned UNAVAILABLE: POST http://127.0.0.1:8003/config/reload after all windows are up
echo [TIPS] Smoke: python scripts\ws_smoke.py ^&^& python scripts\agent_smoke.py --mcp --iot --exit
echo.
pause
