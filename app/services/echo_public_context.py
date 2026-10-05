"""Public answer boundaries and trusted clock context, separate from memory."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIME_ZONE_HEADER = "X-DreamJourney-Time-Zone"

PUBLIC_ANSWER_RULE = (
    "公共常识可以正常回答，不受私人记忆有无的限制。"
    "用户或家人的私人经历、关系、偏好等事实，依据已授权记忆或用户本轮明确提供的信息；"
    "本轮自述不是已确认正式记忆，不得把常识、助手建议或外部资料编造成个人经历。"
    "天气、营业状态、附近店铺等实时信息必须有当前查询依据；未提供查询能力或结果时，"
    "明确说暂时无法实时核实，不编造温度、地址、距离或营业情况。"
    "缺少地点先询问，不能用旧住址推定当前位置。"
)


def clock_context(time_zone: str = "UTC", *, now: datetime | None = None, live: bool = False) -> str:
    # A client may choose its time zone, never the server's wall clock or date.
    name = time_zone if isinstance(time_zone, str) and len(time_zone) <= 64 else "UTC"
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        name, zone = "UTC", ZoneInfo("UTC")
    instant = now if now is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("clock context requires an aware server time")
    local = instant.astimezone(zone)
    day = "一二三四五六日"[local.weekday()]
    boundary = (
        "这是开场参考时间，不是持续更新的时钟；会中跨日或询问精确当前时间时，"
        "只能说明开场时间，不能把它冒充此刻。"
        if live else "这是服务端本轮采样时间，日期和星期按此回答。"
    )
    return (f"【系统时间】{local.isoformat(timespec='seconds')}；时区={name}；"
            f"日期={local.date().isoformat()}；星期{day}。{boundary}")


def is_explicit_public_query(query: str) -> bool:
    """Conservative exception to family memory gating; ambiguous queries stay private."""
    text = re.sub(r"[\s？?。！!，,]", "", str(query or ""))
    # First/second person and family references may refer to personal history.
    if any(cue in text for cue in ("我", "你", "您", "他", "她", "父", "母", "爸", "妈", "爷", "奶", "外公", "外婆", "记得", "以前", "当年")):
        return False
    return bool(re.fullmatch(
        r"(?:今天|明天|昨天|现在)(?:是)?(?:几号|几日|几月几号|几月几日|星期几|周几|礼拜几|什么日期|什么日子|哪一年|几点|几点钟)"
        r"|(?:为什么|为何).{1,60}"
        r"|.{1,40}(?:是什么|是什么意思|的原理是什么)"
        r"|(?:今天|明天|现在)?.{0,24}天气(?:怎么样|如何|怎样)?"
        r"|.{1,30}(?:附近|周边).{0,30}(?:推荐|店|餐厅|饭店|咖啡店|有哪些|有什么)", text))
