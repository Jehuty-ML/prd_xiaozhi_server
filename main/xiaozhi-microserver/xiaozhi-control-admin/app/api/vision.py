"""Vision explain endpoint (auth + multipart; VLLM stub until phase-3 providers)."""

from __future__ import annotations

import base64
from typing import Optional, Tuple

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from loguru import logger

from xiaozhi_common.auth import VisionAuthToken
from xiaozhi_common.runtime_env import is_development

MAX_FILE_SIZE = 5 * 1024 * 1024

_IMAGE_MAGICS = (
    b"\xff\xd8\xff",  # jpeg
    b"\x89PNG\r\n\x1a\n",  # png
    b"GIF87a",
    b"GIF89a",
    b"BM",
    b"II*\x00",
    b"MM\x00*",
    b"RIFF",  # webp container starts with RIFF
)


def _is_valid_image(data: bytes) -> bool:
    return any(data.startswith(m) for m in _IMAGE_MAGICS)


class VisionService:
    def __init__(self, get_config) -> None:
        self._get_config = get_config
        self.apply_config(get_config())

    def apply_config(self, config: dict) -> None:
        self.config = config or {}
        self.auth = VisionAuthToken.from_config(self.config)

    def _cors(self, response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    def _verify(self, request: Request) -> Tuple[bool, Optional[str]]:
        client_id = request.headers.get("Client-Id") or request.headers.get("client-id")
        # Only development may skip Vision JWT for the web test client.
        if client_id == "web_test_client":
            if is_development(self.config):
                device_id = (
                    request.headers.get("Device-Id")
                    or request.headers.get("device-id")
                    or "test_device"
                )
                return True, device_id
            logger.warning("production rejected web_test_client Vision auth bypass")
        auth_header = request.headers.get("Authorization") or ""
        if not auth_header.startswith("Bearer "):
            return False, None
        return self.auth.verify_token(auth_header[7:])

    def create_router(self) -> APIRouter:
        router = APIRouter()

        @router.options("/mcp/vision/explain")
        async def vision_options():
            return self._cors(Response(status_code=204))

        @router.get("/mcp/vision/explain")
        async def vision_get():
            return self._cors(
                PlainTextResponse("Vision interface is running (xiaozhi-control-admin)")
            )

        @router.post("/mcp/vision/explain")
        async def vision_post(
            request: Request,
            question: str = Form(...),
            image: UploadFile = File(...),
        ):
            try:
                ok, token_device_id = self._verify(request)
                if not ok:
                    return self._cors(
                        JSONResponse(
                            {"success": False, "message": "无效的认证token或token已过期"},
                            status_code=401,
                        )
                    )
                device_id = (
                    request.headers.get("Device-Id")
                    or request.headers.get("device-id")
                    or ""
                )
                if device_id != token_device_id:
                    raise ValueError("设备ID与token不匹配")

                image_data = await image.read()
                if not image_data:
                    raise ValueError("图片数据为空")
                if len(image_data) > MAX_FILE_SIZE:
                    raise ValueError(
                        f"图片大小超过限制，最大允许{MAX_FILE_SIZE / 1024 / 1024}MB"
                    )
                if not _is_valid_image(image_data):
                    raise ValueError("不支持的文件格式，请上传有效的图片文件")

                # Phase 2: control-plane + auth real; VLLM providers come with agent phase.
                selected = (self.config.get("selected_module") or {}).get("VLLM")
                image_b64_len = len(base64.b64encode(image_data))
                if not selected:
                    result = (
                        f"[vision-stub] question={question!r} "
                        f"image_b64_bytes={image_b64_len} "
                        f"(configure selected_module.VLLM for real inference)"
                    )
                else:
                    result = (
                        f"[vision-stub:{selected}] question={question!r} "
                        f"image_b64_bytes={image_b64_len}"
                    )

                return self._cors(
                    JSONResponse(
                        {
                            "success": True,
                            "action": "RESPONSE",
                            "response": result,
                        }
                    )
                )
            except ValueError as exc:
                logger.error(f"Vision POST validation: {exc}")
                return self._cors(
                    JSONResponse({"success": False, "message": str(exc)})
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"Vision POST error: {exc}")
                return self._cors(
                    JSONResponse(
                        {"success": False, "message": "request error."},
                        status_code=500,
                    )
                )

        return router
