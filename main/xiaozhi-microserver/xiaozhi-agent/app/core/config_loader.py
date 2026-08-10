"""Load agent YAML + render system prompt template."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from xiaozhi_common.admin_config import deep_merge
from xiaozhi_common.config_files import load_service_config

try:
    from xiaozhi_common.provider_aliases import mirror_provider_aliases
except ImportError:  # pragma: no cover

    def mirror_provider_aliases(config: dict) -> dict:  # type: ignore
        return config


SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = SERVICE_ROOT / "config.yaml"
DEFAULT_PROMPT_PATH = SERVICE_ROOT / "agent-base-prompt.txt"

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


class AgentRuntimeConfig:
    def __init__(self, path: Path | None = None) -> None:
        self.service_root = Path(path).parent if path else SERVICE_ROOT
        self.path = path or DEFAULT_CONFIG_PATH
        self.private_path = self.service_root / "data" / ".config.yaml"
        self.data: dict[str, Any] = {}
        self._local: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        merged, template_path, private_path, _ = load_service_config(self.service_root)
        self.path = template_path
        self.private_path = private_path
        self._local = merged
        self.data = mirror_provider_aliases(dict(merged))
        if not template_path.exists() and not private_path.exists():
            logger.warning(f"Agent config missing under {self.service_root}")

    def apply_remote(self, remote: dict[str, Any], *, reason: str = "") -> None:
        """Merge control-admin / manager-api snapshot over local yaml."""
        if not isinstance(remote, dict) or not remote:
            return
        remote = dict(remote)
        for key in ("manager-api", "manager_api"):
            api = remote.get(key)
            if isinstance(api, dict) and not str(api.get("secret") or "").strip():
                api = dict(api)
                api.pop("secret", None)
                remote[key] = api
        merged = deep_merge(dict(self._local or {}), remote)
        self.data = mirror_provider_aliases(merged)
        selected = (self.data.get("selected_module") or {}).get("LLM")
        remote_llm = (remote.get("selected_module") or {}).get("LLM")
        if remote_llm:
            logger.info(
                f"Agent config applied from admin reason={reason or '-'} LLM={selected}"
            )
        else:
            logger.info(
                f"Agent config applied from admin reason={reason or '-'} "
                f"LLM={selected} (server-base has no LLM; "
                f"per-device LLM comes from agent-models on WS connect)"
            )

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def selected(self) -> dict[str, Any]:
        return self.data.get("selected_module") or {}

    def build_system_prompt(self, config: dict[str, Any] | None = None) -> str:
        data = config if isinstance(config, dict) and config else self.data
        base = (data.get("prompt") or "我是小智。").strip()
        language = data.get("language") or "中文"
        template_name = data.get("prompt_template") or "agent-base-prompt.txt"
        template_path = self.service_root / template_name
        if not template_path.exists():
            template_path = DEFAULT_PROMPT_PATH
        now = datetime.now()
        replacements = {
            "{{base_prompt}}": base,
            "{{language}}": language,
            "{{current_time}}": now.strftime("%H:%M"),
            "{{today_date}}": now.strftime("%Y-%m-%d"),
            "{{today_weekday}}": _WEEKDAYS[now.weekday()],
            "{{lunar_date}}": "",
            "{{local_address}}": "未知",
            "{{weather_info}}": "暂无",
            "{{ dynamic_context }}": "",
            "{{emojiList}}": "😊 😂 😉 😍 🤔 😎 😢 😮 👍 👏",
        }
        if template_path.exists():
            text = template_path.read_text(encoding="utf-8")
            text = text.replace("{%- if emoji_enabled %}", "")
            text = text.replace("{%- else %}", "")
            text = text.replace("{%- endif %}", "")
            for k, v in replacements.items():
                text = text.replace(k, v)
            return text
        return base


runtime_config = AgentRuntimeConfig()
