"""运行环境与安全策略开关。

development：保留 query token、硬编码 key 兜底等联调便利
production：禁止危险兼容行为
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

ENV_DEVELOPMENT = "development"
ENV_PRODUCTION = "production"

_VALID = {ENV_DEVELOPMENT, ENV_PRODUCTION, "dev", "prod", "test"}


def normalize_env(raw: Optional[str]) -> str:
    if not raw:
        return ENV_DEVELOPMENT
    value = str(raw).strip().lower()
    if value in ("prod", "production"):
        return ENV_PRODUCTION
    if value in ("dev", "development", "test", "local"):
        return ENV_DEVELOPMENT
    if value in _VALID:
        return ENV_DEVELOPMENT if value != "production" else ENV_PRODUCTION
    return ENV_DEVELOPMENT


def resolve_environment(config: Optional[Dict[str, Any]] = None) -> str:
    """解析当前环境。

    优先级：环境变量 XIAOZHI_ENV / APP_ENV > server.environment > 默认 development
    """
    for key in ("XIAOZHI_ENV", "APP_ENV"):
        if os.environ.get(key):
            return normalize_env(os.environ.get(key))
    server = (config or {}).get("server") or {}
    return normalize_env(server.get("environment"))


def is_production(config: Optional[Dict[str, Any]] = None) -> bool:
    return resolve_environment(config) == ENV_PRODUCTION


def is_development(config: Optional[Dict[str, Any]] = None) -> bool:
    return not is_production(config)


def allow_query_authorization(config: Optional[Dict[str, Any]] = None) -> bool:
    """生产禁止从 URL query 注入 authorization。"""
    return is_development(config)


def allow_hardcoded_secret_fallback(config: Optional[Dict[str, Any]] = None) -> bool:
    """生产禁止使用代码内硬编码密钥兜底。"""
    return is_development(config)


def _as_optional_bool(value: Any) -> Optional[bool]:
    """解析可选布尔；None/空/auto 表示「未显式配置」。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("", "auto", "null", "none"):
        return None
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    return None


def resolve_auth_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """是否启用连接认证。

    development：尊重 server.auth.enabled（默认 false）
    production：默认强制开启；仅当 auth.allow_insecure_disable=true 时才允许关闭
    """
    auth = ((config or {}).get("server") or {}).get("auth") or {}
    if is_production(config):
        if auth.get("allow_insecure_disable") is True:
            return bool(auth.get("enabled", False))
        return True
    return bool(auth.get("enabled", False))


def resolve_whitelist_bypass_allowed(
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    """白名单设备是否可免 token。

    - 显式 server.auth.allow_whitelist_bypass 优先
    - 未配置时：development=允许免检，production=禁止免检
    """
    auth = ((config or {}).get("server") or {}).get("auth") or {}
    explicit = _as_optional_bool(auth.get("allow_whitelist_bypass"))
    if explicit is not None:
        return explicit
    return is_development(config)


def resolve_devices_allowlist_only(
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    """是否把 allowed_devices 当作「仅列表内可接入」。

    默认 false。生产可显式打开，未列入白名单的设备即使有 token 也拒绝。
    """
    auth = ((config or {}).get("server") or {}).get("auth") or {}
    explicit = _as_optional_bool(auth.get("devices_allowlist_only"))
    if explicit is not None:
        return explicit
    return False


def normalize_allowed_devices(raw: Any) -> set:
    if not raw:
        return set()
    if isinstance(raw, str):
        items = [p.strip() for p in raw.replace(";", ",").split(",")]
        return {x for x in items if x}
    if isinstance(raw, (list, tuple, set)):
        return {str(x).strip() for x in raw if str(x).strip()}
    return set()


def should_bypass_token_for_device(
    config: Optional[Dict[str, Any]],
    device_id: Optional[str],
    allowed_devices: Optional[set] = None,
) -> bool:
    """设备是否可走白名单免检（跳过 token）。"""
    if not resolve_auth_enabled(config):
        return False
    if not resolve_whitelist_bypass_allowed(config):
        return False
    devices = allowed_devices
    if devices is None:
        auth = ((config or {}).get("server") or {}).get("auth") or {}
        devices = normalize_allowed_devices(auth.get("allowed_devices"))
    if not devices or not device_id:
        return False
    return device_id in devices


def is_device_permitted(
    config: Optional[Dict[str, Any]],
    device_id: Optional[str],
    allowed_devices: Optional[set] = None,
) -> bool:
    """allowlist-only 开启且列表非空时，设备必须在白名单内。"""
    if not resolve_devices_allowlist_only(config):
        return True
    devices = allowed_devices
    if devices is None:
        auth = ((config or {}).get("server") or {}).get("auth") or {}
        devices = normalize_allowed_devices(auth.get("allowed_devices"))
    if not devices:
        return True
    return bool(device_id) and device_id in devices


def resolve_auth_kdf_salt(
    config: Optional[Dict[str, Any]] = None, secret_key: str = ""
) -> bytes:
    """PBKDF2 盐值。

    - 若配置了 server.auth.pbkdf2_salt：使用其规范化摘要（部署可指定）
    - production：用 auth_key 派生稳定盐（不写死在源码常量里）
    - development：保留历史固定盐，兼容旧 token
    """
    import hashlib

    auth = ((config or {}).get("server") or {}).get("auth") or {}
    configured = auth.get("pbkdf2_salt") or auth.get("salt")
    if configured is not None:
        text = str(configured).strip()
        if text and text.lower() not in ("null", "none") and "你的" not in text:
            return hashlib.sha256(text.encode("utf-8")).digest()

    if is_production(config):
        sk = secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
        return hashlib.sha256(b"xiaozhi-auth-pbkdf2-v1|" + sk).digest()

    return b"fixed_salt_placeholder"


def get_config_secret(
    section: dict,
    key: str,
    *,
    hardcoded_fallback: str = "",
    config: Optional[Dict[str, Any]] = None,
    empty_means_missing: bool = True,
) -> str:
    """读取密钥：有配置用配置；仅开发环境允许硬编码兜底。"""
    value = ""
    if isinstance(section, dict):
        value = section.get(key) or ""
    value = str(value).strip()
    if value and not (empty_means_missing and value in ("", "null", "None")):
        # 过滤模板占位
        if "你的" in value or "placeholder" in value.lower():
            value = ""
    if value:
        return value
    if hardcoded_fallback and allow_hardcoded_secret_fallback(config):
        return hardcoded_fallback
    return ""
