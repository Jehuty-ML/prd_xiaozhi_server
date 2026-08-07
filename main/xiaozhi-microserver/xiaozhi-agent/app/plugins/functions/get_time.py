from datetime import datetime

from app.tools.register import Action, ActionResponse, ToolType, register_function

get_time_function_desc = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "获取当前日期与时间。用户问现在几点、今天几号时调用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


@register_function("get_time", get_time_function_desc, ToolType.WAIT)
def get_time(**_kwargs):
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    text = (
        f"现在是 {now.strftime('%Y年%m月%d日')} {weekdays[now.weekday()]} "
        f"{now.strftime('%H:%M:%S')}"
    )
    return ActionResponse(Action.REQLLM, text, None)
