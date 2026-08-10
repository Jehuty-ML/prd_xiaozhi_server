"""Shared constants for xiaozhi microservices."""

from __future__ import annotations

# Nacos / discovery service names
ACCESS_SERVICE = "xiaozhi-access-grpc-service"
AGENT_SERVICE = "xiaozhi-agent-grpc-service"
SPEAKER_SERVICE = "xiaozhi-audio-speaker-grpc-service"
PREPROCESS_SERVICE = "xiaozhi-audio-preprocess-grpc-service"
RECEIVER_SERVICE = "xiaozhi-audio-receiver-grpc-service"
CONTROL_ADMIN_SERVICE = "xiaozhi-control-admin-grpc-service"
# Deprecated alias (pre-rename model-admin)
MODEL_ADMIN_SERVICE = CONTROL_ADMIN_SERVICE

# Default local static ports (dev)
DEFAULT_PORTS = {
    ACCESS_SERVICE: 50051,
    AGENT_SERVICE: 50052,
    SPEAKER_SERVICE: 50053,
    PREPROCESS_SERVICE: 50054,
    RECEIVER_SERVICE: 50055,
    CONTROL_ADMIN_SERVICE: 50056,
}

# Align with monolith xiaozhi-server: WS :8000, OTA/HTTP :8003
ACCESS_HTTP_PORT = 8000
CONTROL_ADMIN_HTTP_PORT = 8003
MODEL_ADMIN_HTTP_PORT = CONTROL_ADMIN_HTTP_PORT
