"""Control-admin OTA / Vision fixes ported from monolith tip commits."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

MICRO_ROOT = Path(__file__).resolve().parents[1]
ADMIN_ROOT = MICRO_ROOT / "xiaozhi-control-admin"


def _use_admin_app() -> None:
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]
    paths = [
        str(ADMIN_ROOT),
        str(MICRO_ROOT / "common"),
        str(MICRO_ROOT / "common" / "generated"),
    ]
    for s in paths:
        if s in sys.path:
            sys.path.remove(s)
    for s in reversed(paths):
        sys.path.insert(0, s)


_use_admin_app()


@pytest.fixture(autouse=True)
def _admin_path():
    _use_admin_app()
    yield


def test_ota_websocket_splits_semicolon_list(monkeypatch):
    from app.api.ota import OtaService

    cfg = {
        "server": {
            "websocket": "ws://a.example:8000/xiaozhi/v1/;ws://b.example:8000/xiaozhi/v1/",
            "port": 8000,
            "auth": {},
        }
    }
    svc = OtaService(lambda: cfg)
    monkeypatch.setattr("app.api.ota.random.choice", lambda seq: seq[0])
    url = svc._websocket_url()
    assert ";" not in url
    assert url == "ws://a.example:8000/xiaozhi/v1/"


def test_ota_websocket_single_url_unchanged():
    from app.api.ota import OtaService

    cfg = {
        "server": {
            "websocket": "ws://only.example:8000/xiaozhi/v1/",
            "port": 8000,
            "auth": {},
        }
    }
    svc = OtaService(lambda: cfg)
    assert svc._websocket_url() == "ws://only.example:8000/xiaozhi/v1/"


def test_ota_websocket_placeholder_falls_back(monkeypatch):
    from app.api.ota import OtaService

    cfg = {
        "server": {
            "websocket": "ws://你的服务器地址:8000/xiaozhi/v1/",
            "port": 9000,
            "auth": {},
        }
    }
    svc = OtaService(lambda: cfg)
    monkeypatch.setattr("app.api.ota._local_ip", lambda: "127.0.0.1")
    assert svc._websocket_url() == "ws://127.0.0.1:9000/xiaozhi/v1/"


def test_vision_web_test_client_allowed_in_development():
    from app.api.vision import VisionService

    svc = VisionService(lambda: {"server": {"environment": "development", "auth_key": "k"}})
    req = MagicMock()
    req.headers.get = lambda key, default=None: {
        "Client-Id": "web_test_client",
        "client-id": "web_test_client",
        "Device-Id": "dev1",
        "device-id": "dev1",
    }.get(key, default)
    ok, device_id = svc._verify(req)
    assert ok is True
    assert device_id == "dev1"


def test_vision_web_test_client_rejected_in_production():
    from app.api.vision import VisionService

    svc = VisionService(lambda: {"server": {"environment": "production", "auth_key": "k"}})
    req = MagicMock()
    req.headers.get = lambda key, default=None: {
        "Client-Id": "web_test_client",
        "client-id": "web_test_client",
        "Device-Id": "dev1",
        "device-id": "dev1",
        "Authorization": "",
    }.get(key, default)
    ok, device_id = svc._verify(req)
    assert ok is False
    assert device_id is None
