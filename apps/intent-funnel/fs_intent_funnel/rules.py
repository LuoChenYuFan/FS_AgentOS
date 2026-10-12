"""Keyword scores. One clear hit is a high probability; two hits split the mass."""

from __future__ import annotations

from fs_agent_runtime.planner import is_place_to_place

_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("chat", ("哪些技能", "有什么技能", "你会什么", "你能做什么", "你是谁")),
    ("send_email", ("发邮件", "发一封邮件", "发封邮件", "发送邮件", "发信", "给你的主人", "发给主人")),
    ("weather", ("查天气", "天气预报")),
    ("itinerary", ("旅游攻略", "规划行程")),
    ("knowledge", ("员工手册", "规章制度")),
)
# 这些是要办的事。句子里只是提到张博洋、没有这些动作时，才改走知识库。
_ACTION_MARKERS = (
    "发邮件",
    "发一封邮件",
    "发封邮件",
    "发送邮件",
    "发信",
    "查天气",
    "天气预报",
    "查酒店",
    "酒店",
    "查路线",
    "路线",
    "旅游攻略",
    "规划行程",
)


def rule_scores(text: str) -> dict[str, float]:
    if is_place_to_place(text):
        return {"itinerary": 0.95}
    if "张博洋" in text and not any(word in text for word in _ACTION_MARKERS):
        return {"knowledge": 0.95}
    hits = [name for name, words in _RULES if any(word in text for word in words)]
    if not hits:
        return {}
    if len(hits) == 1:
        return {hits[0]: 0.9}
    share = 1.0 / len(hits)
    return {name: share for name in hits}
