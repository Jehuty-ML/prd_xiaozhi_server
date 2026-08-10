"""Volcengine Doubao TTS — text → opus frames."""

from __future__ import annotations

import base64
import json
import uuid
from typing import List

from loguru import logger

from app.providers.tts.audio_codec import audio_bytes_to_opus_frames
from app.providers.tts.base import TTSProviderBase


class DoubaoTTS(TTSProviderBase):
    output_format = "opus"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        cfg = self.config
        if cfg.get("appid"):
            try:
                self.appid = int(cfg.get("appid"))
            except (TypeError, ValueError):
                self.appid = cfg.get("appid")
        else:
            self.appid = ""
        self.access_token = cfg.get("access_token") or ""
        self.cluster = cfg.get("cluster") or "volcano_tts"
        self.voice = cfg.get("private_voice") or cfg.get("voice") or "BV001_streaming"
        self.audio_file_type = cfg.get("format") or "wav"
        self.speed_ratio = float(cfg.get("speed_ratio") or 1.0)
        self.volume_ratio = float(cfg.get("volume_ratio") or 1.0)
        self.pitch_ratio = float(cfg.get("pitch_ratio") or 1.0)
        self.api_url = (
            cfg.get("api_url") or "https://openspeech.bytedance.com/api/v1/tts"
        )
        authorization = cfg.get("authorization") or "Bearer;"
        self.header = {"Authorization": f"{authorization}{self.access_token}"}

    def synthesize_opus_frames(self, text: str) -> List[bytes]:
        text = (text or "").strip()
        if not text:
            return []
        try:
            audio = self._fetch_audio(text)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"DoubaoTTS failed: {exc}")
            return []
        return audio_bytes_to_opus_frames(
            audio,
            file_type=self.audio_file_type,
            sample_rate=self.sample_rate,
            frame_ms=self.frame_duration_ms,
        )

    def _fetch_audio(self, text: str) -> bytes:
        import requests

        request_json = {
            "app": {
                "appid": f"{self.appid}",
                "token": self.access_token,
                "cluster": self.cluster,
            },
            "user": {"uid": "1"},
            "audio": {
                "voice_type": self.voice,
                "encoding": self.audio_file_type,
                "speed_ratio": self.speed_ratio,
                "volume_ratio": self.volume_ratio,
                "pitch_ratio": self.pitch_ratio,
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": text,
                "text_type": "plain",
                "operation": "query",
                "with_frontend": 1,
                "frontend_type": "unitTson",
            },
        }
        resp = requests.post(
            self.api_url, data=json.dumps(request_json), headers=self.header, timeout=30
        )
        body = resp.json()
        if "data" in body:
            return base64.b64decode(body["data"])
        raise RuntimeError(
            f"DoubaoTTS status={resp.status_code} body={resp.content[:200]!r}"
        )
