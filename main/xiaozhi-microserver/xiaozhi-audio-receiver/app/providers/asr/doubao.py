"""Volcengine Doubao (ByteDance) non-stream ASR — batch PCM → text."""

from __future__ import annotations

import asyncio
import gzip
import json
import time
import uuid
from typing import Any, Iterator, Optional, Tuple

from loguru import logger

from app.providers.asr.base import ASRProviderBase

CLIENT_FULL_REQUEST = 0b0001
CLIENT_AUDIO_ONLY_REQUEST = 0b0010
NO_SEQUENCE = 0b0000
NEG_SEQUENCE = 0b0010
SERVER_FULL_RESPONSE = 0b1001
SERVER_ACK = 0b1011
SERVER_ERROR_RESPONSE = 0b1111
NO_SERIALIZATION = 0b0000
JSON = 0b0001
GZIP = 0b0001


def parse_response(res: bytes) -> dict:
    header_size = res[0] & 0x0F
    message_type = res[1] >> 4
    serialization_method = res[2] >> 4
    message_compression = res[2] & 0x0F
    payload = res[header_size * 4 :]
    result: dict = {}
    payload_msg = None
    payload_size = 0
    if message_type == SERVER_FULL_RESPONSE:
        payload_size = int.from_bytes(payload[:4], "big", signed=True)
        payload_msg = payload[4:]
    elif message_type == SERVER_ACK:
        seq = int.from_bytes(payload[:4], "big", signed=True)
        result["seq"] = seq
        if len(payload) >= 8:
            payload_size = int.from_bytes(payload[4:8], "big", signed=False)
            payload_msg = payload[8:]
    elif message_type == SERVER_ERROR_RESPONSE:
        code = int.from_bytes(payload[:4], "big", signed=False)
        result["code"] = code
        payload_size = int.from_bytes(payload[4:8], "big", signed=False)
        payload_msg = payload[8:]
    if payload_msg is None:
        return result
    if message_compression == GZIP:
        payload_msg = gzip.decompress(payload_msg)
    if serialization_method == JSON:
        payload_msg = json.loads(str(payload_msg, "utf-8"))
    elif serialization_method != NO_SERIALIZATION:
        payload_msg = str(payload_msg, "utf-8")
    result["payload_msg"] = payload_msg
    result["payload_size"] = payload_size
    return result


class DoubaoASR(ASRProviderBase):
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        cfg = self.config
        self.appid = cfg.get("appid")
        self.cluster = cfg.get("cluster") or "volcengine_input_common"
        self.access_token = cfg.get("access_token") or ""
        self.boosting_table_name = cfg.get("boosting_table_name") or ""
        self.correct_table_name = cfg.get("correct_table_name") or ""
        self.host = cfg.get("host") or "openspeech.bytedance.com"
        self.ws_url = cfg.get("ws_url") or f"wss://{self.host}/api/v2/asr"
        self.success_code = 1000
        self.seg_duration = int(cfg.get("seg_duration") or 15000)

    @staticmethod
    def _generate_header(
        message_type=CLIENT_FULL_REQUEST, message_type_specific_flags=NO_SEQUENCE
    ) -> bytearray:
        header = bytearray()
        header.append((0b0001 << 4) | 1)
        header.append((message_type << 4) | message_type_specific_flags)
        header.append((0b0001 << 4) | 0b0001)
        header.append(0x00)
        return header

    def _construct_request(self, reqid: str, sample_rate: int) -> dict:
        return {
            "app": {
                "appid": f"{self.appid}",
                "cluster": self.cluster,
                "token": self.access_token,
            },
            "user": {"uid": str(uuid.uuid4())},
            "request": {
                "reqid": reqid,
                "show_utterances": False,
                "sequence": 1,
                "boosting_table_name": self.boosting_table_name,
                "correct_table_name": self.correct_table_name,
            },
            "audio": {
                "format": "raw",
                "rate": sample_rate,
                "language": "zh-CN",
                "bits": 16,
                "channel": 1,
                "codec": "raw",
            },
        }

    @staticmethod
    def slice_data(data: bytes, chunk_size: int) -> Iterator[Tuple[bytes, bool]]:
        data_len = len(data)
        offset = 0
        while offset + chunk_size < data_len:
            yield data[offset : offset + chunk_size], False
            offset += chunk_size
        yield data[offset:data_len], True

    async def _send_request(
        self, audio_data: bytes, segment_size: int, sample_rate: int
    ) -> Optional[str]:
        import websockets

        try:
            auth_header = {"Authorization": f"Bearer; {self.access_token}"}
            async with websockets.connect(
                self.ws_url, additional_headers=auth_header
            ) as websocket:
                request_params = self._construct_request(
                    str(uuid.uuid4()), sample_rate
                )
                payload_bytes = gzip.compress(
                    str.encode(json.dumps(request_params))
                )
                full_client_request = self._generate_header()
                full_client_request.extend(len(payload_bytes).to_bytes(4, "big"))
                full_client_request.extend(payload_bytes)
                await websocket.send(full_client_request)
                result = parse_response(await websocket.recv())
                if (
                    "payload_msg" in result
                    and result["payload_msg"].get("code") != self.success_code
                    and result["payload_msg"].get("code") != 1013
                ):
                    logger.error(f"DoubaoASR error: {result}")
                    return None

                for _seq, (chunk, last) in enumerate(
                    self.slice_data(audio_data, segment_size), 1
                ):
                    flags = NEG_SEQUENCE if last else NO_SEQUENCE
                    audio_only_request = self._generate_header(
                        message_type=CLIENT_AUDIO_ONLY_REQUEST,
                        message_type_specific_flags=flags,
                    )
                    payload_bytes = gzip.compress(chunk)
                    audio_only_request.extend(
                        len(payload_bytes).to_bytes(4, "big")
                    )
                    audio_only_request.extend(payload_bytes)
                    await websocket.send(audio_only_request)

                result = parse_response(await websocket.recv())
                payload = result.get("payload_msg") or {}
                code = payload.get("code")
                if code == self.success_code:
                    results = payload.get("result") or []
                    if results:
                        return results[0].get("text") or ""
                    return ""
                if code == 1013:
                    return ""
                logger.error(f"DoubaoASR error: {result}")
                return None
        except Exception as exc:  # noqa: BLE001
            logger.error(f"DoubaoASR request failed: {exc}")
            return None

    def recognize(self, pcm: bytes, *, sample_rate: int = 16000) -> str:
        if not pcm:
            return ""
        size_per_sec = 1 * 2 * sample_rate
        segment_size = int(size_per_sec * self.seg_duration / 1000)
        start = time.time()

        async def _run() -> Optional[str]:
            return await self._send_request(pcm, segment_size, sample_rate)

        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    text = asyncio.run(_run())
                else:
                    text = loop.run_until_complete(_run())
            except RuntimeError:
                text = asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001
            logger.error(f"DoubaoASR recognize failed: {exc}")
            return ""

        if text:
            logger.debug(f"DoubaoASR {time.time() - start:.3f}s text={text!r}")
            return text
        return ""
