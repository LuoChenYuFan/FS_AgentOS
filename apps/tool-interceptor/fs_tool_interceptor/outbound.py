"""Outbound checks. Retrieved text is data; mail still needs a person and a clean body."""

from __future__ import annotations

import re

DEFAULT_RECIPIENTS = frozenset({"小明", "张三", "李四", "老张"})
_OWNER_ALIASES = frozenset({"你的主人", "我的主人", "主人", "东家", "老板", "张博洋"})

_INJECTION = (
    "忽略之前的指令",
    "ignore previous instructions",
    "ignore all previous",
)
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
_SECRET = re.compile(r"sk-[A-Za-z0-9]|AKIA[0-9A-Z]{16}")


def outbound_block(
    to: str, body: str, *, recipients: frozenset[str] = DEFAULT_RECIPIENTS
) -> str:
    """Empty string means the draft may be sent. A reason means it must not."""

    name = to.strip()
    if name and name not in recipients and name not in _OWNER_ALIASES:
        return f"收件人{name}不在外发名单里，邮件未发送"
    lowered = body.lower()
    if any(mark in lowered for mark in _INJECTION):
        return "邮件正文含有指令注入，邮件未发送"
    if _ID_CARD.search(body) or _BANK_CARD.search(body) or _SECRET.search(body):
        return "邮件正文含有敏感信息，邮件未发送"
    return ""
