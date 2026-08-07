"""HMAC device token (WS / OTA) and vision JWT helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from xiaozhi_common.runtime_env import resolve_auth_kdf_salt


class AuthenticationError(Exception):
    """Auth failure."""


class AuthManager:
    """HMAC-SHA256 token for client_id + device_id (WS / OTA)."""

    def __init__(self, secret_key: str, expire_seconds: int = 60 * 60 * 24 * 30):
        if not expire_seconds or expire_seconds < 0:
            self.expire_seconds = 60 * 60 * 24 * 30
        else:
            self.expire_seconds = expire_seconds
        self.secret_key = secret_key or ""

    def _sign(self, content: str) -> str:
        sig = hmac.new(
            self.secret_key.encode("utf-8"), content.encode("utf-8"), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")

    def generate_token(self, client_id: str, username: str) -> str:
        ts = int(time.time())
        content = f"{client_id}|{username}|{ts}"
        signature = self._sign(content)
        return f"{signature}.{ts}"

    def verify_token(self, token: str, client_id: str, username: str) -> bool:
        try:
            sig_part, ts_str = token.split(".")
            ts = int(ts_str)
            if int(time.time()) - ts > self.expire_seconds:
                return False
            expected_sig = self._sign(f"{client_id}|{username}|{ts}")
            return hmac.compare_digest(sig_part, expected_sig)
        except Exception:
            return False


class VisionAuthToken:
    """AES-GCM + JWT token used by /mcp/vision/explain."""

    def __init__(self, secret_key: str, salt: bytes | None = None):
        self.secret_key = (
            secret_key.encode() if isinstance(secret_key, str) else secret_key
        )
        self._salt = salt if salt is not None else b"fixed_salt_placeholder"
        self.encryption_key = self._derive_key(32)

    @classmethod
    def from_config(cls, config: dict) -> "VisionAuthToken":
        secret_key = (config.get("server") or {}).get("auth_key") or ""
        salt = resolve_auth_kdf_salt(config, secret_key)
        return cls(secret_key, salt=salt)

    def _derive_key(self, length: int) -> bytes:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=length,
            salt=self._salt,
            iterations=100000,
            backend=default_backend(),
        )
        return kdf.derive(self.secret_key)

    def _encrypt_payload(self, payload: dict) -> str:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        payload_json = json.dumps(payload)
        iv = os.urandom(12)
        cipher = Cipher(
            algorithms.AES(self.encryption_key),
            modes.GCM(iv),
            backend=default_backend(),
        )
        encryptor = cipher.encryptor()
        ciphertext = encryptor.update(payload_json.encode()) + encryptor.finalize()
        encrypted_data = iv + ciphertext + encryptor.tag
        return base64.urlsafe_b64encode(encrypted_data).decode()

    def _decrypt_payload(self, encrypted_data: str) -> dict:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        data = base64.urlsafe_b64decode(encrypted_data.encode())
        iv, tag, ciphertext = data[:12], data[-16:], data[12:-16]
        cipher = Cipher(
            algorithms.AES(self.encryption_key),
            modes.GCM(iv, tag),
            backend=default_backend(),
        )
        decryptor = cipher.decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        return json.loads(plaintext.decode())

    def generate_token(self, device_id: str) -> str:
        import jwt

        expire_time = datetime.now(timezone.utc) + timedelta(hours=1)
        payload = {"device_id": device_id, "exp": expire_time.timestamp()}
        encrypted_payload = self._encrypt_payload(payload)
        return jwt.encode(
            {"data": encrypted_payload, "exp": expire_time},
            self.secret_key,
            algorithm="HS256",
        )

    def verify_token(self, token: str) -> Tuple[bool, Optional[str]]:
        import jwt

        try:
            decoded = jwt.decode(token, self.secret_key, algorithms=["HS256"])
            payload = self._decrypt_payload(decoded["data"])
            if payload.get("exp", 0) < datetime.now(timezone.utc).timestamp():
                return False, None
            return True, payload.get("device_id")
        except Exception:
            return False, None
