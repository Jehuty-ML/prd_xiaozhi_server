"""Production control-plane auth gate (phase-6)."""

from __future__ import annotations

import hmac
import secrets
from typing import Callable, Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from loguru import logger

from xiaozhi_common.runtime_env import is_production

# Paths that stay public even in production (probes / metrics scrape).
PUBLIC_PATHS = {
    "/health",
    "/ready",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def _extract_token(request: Request) -> str:
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # Admin header used by ops tooling
    return (
        request.headers.get("x-admin-token")
        or request.headers.get("X-Admin-Token")
        or ""
    ).strip()


def resolve_admin_token(config: dict) -> str:
    server = (config or {}).get("server") or {}
    admin = server.get("admin") or {}
    token = (
        admin.get("token")
        or server.get("admin_token")
        or server.get("auth_key")
        or ""
    )
    api = (config or {}).get("manager-api") or (config or {}).get("manager_api") or {}
    if not token:
        token = str(api.get("secret") or "")
    return str(token or "").strip()


def admin_auth_required(config: dict) -> bool:
    """Require token for sensitive admin routes in production (or when explicitly enabled)."""
    server = (config or {}).get("server") or {}
    admin = server.get("admin") or {}
    if admin.get("require_auth") is True:
        return True
    if admin.get("require_auth") is False:
        return False
    return is_production(config)


def create_admin_auth_middleware(get_config: Callable[[], dict]):
    async def middleware(request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        # Always allow OTA / vision device endpoints (device auth is separate).
        if (
            path in PUBLIC_PATHS
            or path.startswith("/xiaozhi/ota")
            or path.startswith("/mcp/vision")
        ):
            return await call_next(request)

        cfg = get_config() or {}
        if not admin_auth_required(cfg):
            return await call_next(request)

        expected = resolve_admin_token(cfg)
        if not expected:
            logger.error("production admin gate: missing admin token / auth_key / secret")
            return JSONResponse(
                {
                    "status": "error",
                    "message": "admin token not configured (set server.admin.token or auth_key)",
                },
                status_code=503,
            )

        provided = _extract_token(request)
        if not provided or not hmac.compare_digest(provided, expected):
            return JSONResponse(
                {"status": "error", "message": "unauthorized"},
                status_code=401,
            )
        return await call_next(request)

    return middleware


def generate_dev_admin_hint() -> str:
    return secrets.token_urlsafe(24)
