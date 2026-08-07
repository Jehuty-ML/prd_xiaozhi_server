"""Shared constants for xiaozhi microservices."""

from __future__ import annotations

# Nacos / discovery service names
ACCESS_SERVICE = "xiaozhi-access-grpc-service"
AGENT_SERVICE = "xiaozhi-agent-grpc-service"
SPEAKER_SERVICE = "xiaozhi-audio-speaker-grpc-service"
PREPROCESS_SERVICE = "xiaozhi-audio-preprocess-grpc-service"
RECEIVER_SERVICE = "xiaozhi-audio-receiver-grpc-service"
MODEL_ADMIN_SERVICE = "xiaozhi-model-admin-grpc-service"

# Default local static ports (dev)
DEFAULT_PORTS = {
    ACCESS_SERVICE: 50051,
    AGENT_SERVICE: 50052,
    SPEAKER_SERVICE: 50053,
    PREPROCESS_SERVICE: 50054,
    RECEIVER_SERVICE: 50055,
    MODEL_ADMIN_SERVICE: 50056,
}

# 8003 is commonly used by legacy xiaozhi-server HTTP; use 8103 while both coexist.
ACCESS_HTTP_PORT = 8103
MODEL_ADMIN_HTTP_PORT = 8004
