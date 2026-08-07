from __future__ import annotations

from fastapi import FastAPI
from app.core.handler.config_store import ConfigStore


def create_http_app(store: ConfigStore) -> FastAPI:
    app = FastAPI(title="xiaozhi-model-admin", version="0.1.0")

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "xiaozhi-model-admin",
            "phase": 1,
        }

    @app.get("/config")
    async def get_config(key: str = ""):
        return store.get(key) if key else store.get()

    @app.post("/config/reload")
    async def reload_config():
        store.reload(reason="http")
        return {"status": "reloaded"}

    # Placeholders for phase-2 OTA / vision routes
    @app.get("/xiaozhi/ota/")
    async def ota_placeholder():
        return {
            "status": "not_implemented",
            "note": "OTA moves here from xiaozhi-server in phase 2",
        }

    @app.post("/mcp/vision/explain")
    async def vision_placeholder():
        return {
            "status": "not_implemented",
            "note": "Vision moves here from xiaozhi-server in phase 2",
        }

    return app
