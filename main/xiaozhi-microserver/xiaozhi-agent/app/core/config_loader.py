"""Load agent YAML + render system prompt template."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = SERVICE_ROOT / "config.yaml"
DEFAULT_PROMPT_PATH = SERVICE_ROOT / "agent-base-prompt.txt"

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


class AgentRuntimeConfig:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_CONFIG_PATH
        self.data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            logger.warning(f"Agent config missing: {self.path}")
            self.data = {}
            return
        with self.path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            loaded = {}
        self.data = loaded
        logger.info(f"Agent config loaded from {self.path}")

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def selected(self) -> dict[str, Any]:
        return self.data.get("selected_module") or {}

    def build_system_prompt(self) -> str:
        base = (self.data.get("prompt") or "我是小智。").strip()
        language = self.data.get("language") or "中文"
        template_name = self.data.get("prompt_template") or "agent-base-prompt.txt"
        template_path = SERVICE_ROOT / template_name
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
            # Simple jinja-ish: strip emoji conditional blocks → enable emoji path
            text = text.replace("{%- if emoji_enabled %}", "")
            text = text.replace("{%- else %}", "")
            text = text.replace("{%- endif %}", "")
            for k, v in replacements.items():
                text = text.replace(k, v)
            return text
        return base


runtime_config = AgentRuntimeConfig()
