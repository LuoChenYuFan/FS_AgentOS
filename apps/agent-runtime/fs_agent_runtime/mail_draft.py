"""Prepare a mail draft. SMTP only runs after a person approves."""

from __future__ import annotations

from typing import Any

_MISSING_BODY = "还没有邮件内容，请说一下要发送的内容。"


def prepare_mail_draft(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    arguments = arguments or {}
    to = str(
        arguments.get("收件人")
        or arguments.get("to")
        or arguments.get("receiver")
        or arguments.get("email")
        or ""
    ).strip()
    subject = str(
        arguments.get("主题")
        or arguments.get("subject")
        or arguments.get("title")
        or ""
    ).strip()
    body = str(
        arguments.get("正文")
        or arguments.get("正文模板")
        or arguments.get("body")
        or arguments.get("content")
        or ""
    ).strip()
    draft = {
        "to": to,
        "subject": subject,
        "body": body,
        "收件人": to,
        "主题": subject,
        "正文": body,
    }
    if not body:
        return {
            "summary": _MISSING_BODY,
            "sent": False,
            "needs_body": True,
            "draft": draft,
        }
    if not subject:
        subject = body[:40]
        draft["subject"] = subject
        draft["主题"] = subject
    preview = _preview(to=to, subject=subject, body=body)
    return {
        "summary": preview,
        "sent": False,
        "needs_approval": True,
        "draft": draft,
    }


def _preview(*, to: str, subject: str, body: str) -> str:
    who = to or "收件人"
    return (
        f"将发送以下邮件：\n"
        f"收件人：{who}\n"
        f"主题：{subject}\n"
        f"正文：\n{body}\n\n"
        f"请确认是否发送。"
    )
