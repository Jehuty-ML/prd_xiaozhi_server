"""Simplified weather plugin — Open-Meteo geocoding + forecast (no API key)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from loguru import logger

from app.tools.register import Action, ActionResponse, ToolType, register_function

if TYPE_CHECKING:
    from app.core.session import Session

GET_WEATHER_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "获取某个地点的天气。用户应提供位置，如杭州天气。"
            "若用户未指明城市，可传空 location。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "地点名，例如杭州",
                },
                "lang": {
                    "type": "string",
                    "description": "语言 code，默认 zh_CN",
                },
            },
            "required": [],
        },
    },
}


@register_function("get_weather", GET_WEATHER_FUNCTION_DESC, ToolType.WAIT)
def get_weather(location: str | None = None, lang: str = "zh_CN", **_kwargs):
    city = (location or "杭州").strip() or "杭州"
    try:
        with httpx.Client(timeout=10.0) as client:
            geo = client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "zh"},
            )
            geo.raise_for_status()
            results = (geo.json() or {}).get("results") or []
            if not results:
                return ActionResponse(
                    Action.REQLLM, f"找不到地点「{city}」的天气信息", None
                )
            place = results[0]
            lat, lon = place["latitude"], place["longitude"]
            name = place.get("name") or city
            forecast = client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current_weather": True,
                    "timezone": "auto",
                },
            )
            forecast.raise_for_status()
            current = (forecast.json() or {}).get("current_weather") or {}
            temp = current.get("temperature")
            wind = current.get("windspeed")
            code = current.get("weathercode")
            text = (
                f"{name}当前气温约 {temp}℃，风速 {wind} km/h，天气代码 {code}。"
                f"（lang={lang}）请用口语简短告诉用户。"
            )
            return ActionResponse(Action.REQLLM, text, None)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"get_weather failed: {exc}")
        return ActionResponse(
            Action.REQLLM, f"查询{city}天气失败：{exc}", None
        )
