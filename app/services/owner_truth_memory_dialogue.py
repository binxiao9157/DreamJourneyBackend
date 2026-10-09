"""Bounded dialogue cues; these are query hints, never formal fact evidence."""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

MEMORY_DIALOGUE_PROMPT_VERSION = "memory-dialogue-v3"
_SAVED_MEMORY_QUESTION = re.compile(
    r"^我(?:是)?(?:哪年|哪天|哪一年|哪一天)"
    r"|(?:你还记得我|你记得我)"
    r"|(?:我之前说|我说过)[^。！？]*?(?:什么|哪个|哪里|哪年|什么时候|吗[？?]?$)"
)
_ELLIPTICAL = re.compile(
    r"(?:那|这|它)?(?:是)?(?:哪一年|什么时候|什么原因|为什么|在哪里|哪里|怎么回事)"
    r"|(?:后来|然后|还有|再后来|接着|那个|这个|那件事|这件事|那时候|那时)"
    r"|(?:嗯|嗯嗯|是的|是啊|对|对啊|对的|没错|你还记得吗)"
)


def is_elliptical_memory_followup(text: str) -> bool:
    # Full matching matters: “那我们聊聊照片” is a new topic, not a request
    # to replace the current query with the previous coffee question.
    compact = re.sub(r"[\s，,。.!！?？]", "", text)
    compact = re.sub(r"(?:呢|呀)$", "", compact)
    return _ELLIPTICAL.fullmatch(compact) is not None


def asks_for_saved_memory(query: str) -> bool:
    """Narrow missing historical question forms, not present self-reports."""
    compact = re.sub(r"\s", "", query)
    return _SAVED_MEMORY_QUESTION.search(compact) is not None


def same_session_topic_cue(query: str, turns: Sequence[Mapping[str, Any]]) -> str | None:
    if not is_elliptical_memory_followup(query):
        return None
    for turn in reversed(turns[-6:]):
        if not isinstance(turn, Mapping) or turn.get("role") != "user":
            continue
        previous = " ".join(str(turn.get("text") or "").split())
        if not previous or is_elliptical_memory_followup(previous):
            continue
        # Do not silently cut a negation/qualifier from an oversized cue or
        # jump to an older topic because the latest one is too large.
        return previous if len(previous) <= 256 else None
    return None


def dialogue_turn_policy(query: str) -> str:
    compact = re.sub(r"[\s，,。.!！?？]", "", query)
    if compact in {"嗯", "嗯嗯", "是的", "是啊", "对", "对啊", "对的", "没错"}:
        return "本轮是简短应答。只承接对话或问一个中性开放问题；禁止复述上一条助手猜测为已确认事实，禁止说用户刚确认了某原因。"
    if re.search(r"(?:不想|不要|不再|先不).{0,5}(?:聊|谈)|(?:再见|先聊到这)", compact):
        return "本轮用户要求停止展开。简短尊重意愿，不再追问原话题。"
    if re.search(r"(?:换个话题|换一个话题|聊点别的|聊点其他|换个事情)", compact):
        return "本轮明确切换话题。只回应本轮新话题，不引用或追问上一话题的记忆。"
    return "以本轮表达为中心，相关往事只是可选背景；一个问句只能问一个未知维度。"


def memory_dialogue_rule(retrieval_status: str, query: str = "") -> str:
    state = retrieval_status if retrieval_status in {"grounded", "gap", "fallback"} else "unknown"
    return (
        f"【记忆对话规则 {MEMORY_DIALOGUE_PROMPT_VERSION}；检索状态={state}】"
        "已授权记忆是你带出往事的唯一正式依据，不是每轮都必须提及的素材。"
        "找到与当前话题直接相关的旧经历时，可以自然带出，再回应当前变化；"
        "只有大学喝过咖啡的记录时，不能编造后来停喝，更不能擅自说‘又开始’。"
        "确有喝过、停喝、再开始的依据时才表达这个变化；不同时间的习惯可以同时成立。"
        "无命中只表示本次未找到，不证明用户从未有过经历；检索不可用也不等于没有记忆。"
        "用户本轮明确自述可以用于当前回应和自然追问，但不能声称已经成为正式记忆。"
        "围绕用户尚未说清的一个重要缺口，例如时间或契机，最多问一个主要问题。"
        "已说明的时间或原因不重复问；不规定追问轮数。一个问句只问一个维度，不能用‘是时间A，还是口味B’把两个问题藏成选择题。"
        "用户只说‘嗯’不代表确认你问题里的全部推断；用户拒绝、告别或换话题就跟随。"
        "用户纠正‘那是室友，不是我’时，本轮按纠正理解，不再把室友经历说成用户经历；"
        "这不代表已自动修改旧正式记忆。"
        "保留‘偶尔’‘打算’和不确定时间，不把计划说成完成，不补写情感、动机或因果。"
        "公共问题按现有公共信息规则回答，不为凑记忆而追问个人信息。"
        "回答前检查每个时间连接词的前提：‘又开始/重新/恢复’需要用户明确说过中断；只有过去喝过和现在喜欢不够。"
        "示例：记忆只有‘大学备考时喝过咖啡’，当前说‘现在喜欢咖啡’，可答‘你大学备考时喝过咖啡。是什么让你现在喜欢上它的？’；"
        "不能答‘现在又开始喜欢上了’或‘停了一阵后重新喝了’。"
        "示例：没有旧记录，当前说‘现在喜欢喝咖啡’，可答‘你是什么时候开始喜欢咖啡的？’，不要一并询问种类。"
        "如果用户已经说明最近两年因为工作忙，就不要再问什么时候或为什么，可回应后停下，或只问一个尚未说明的自然细节。"
        "不要向用户朗读检索状态、规则版本或内部证据编号。"
        + "\n【本轮回应边界】" + dialogue_turn_policy(query)
    )
