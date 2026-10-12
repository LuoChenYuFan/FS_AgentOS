"""向 SMTP_WHITELIST 中的地址发一封纯文本邮件。其它收件人一律拒绝。"""

from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 4000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_OWNER_ALIASES = (
    "你的主人",
    "我的主人",
    "主人",
    "东家",
    "老板",
    "张博洋",
)
_INJECTION = (
    "忽略之前的指令",
    "ignore previous instructions",
    "ignore all previous",
    "jailbreak",
    "越狱",
)
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
_SECRET = re.compile(r"sk-[A-Za-z0-9]|AKIA[0-9A-Z]{16}")


def load_local_env() -> None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        env_file = parent / ".env"
        if env_file.is_file() and (parent / "pyproject.toml").is_file():
            _apply_env(env_file)
            return


def run(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """技能入口。arguments 用 收件人、主题、正文。"""
    arguments = arguments or {}
    to_raw = str(
        arguments.get("收件人")
        or arguments.get("to")
        or arguments.get("receiver")
        or arguments.get("email")
        or ""
    ).strip()
    subject = str(arguments.get("主题") or arguments.get("subject") or arguments.get("title") or "").strip()
    body = str(
        arguments.get("正文")
        or arguments.get("正文模板")
        or arguments.get("body")
        or arguments.get("content")
        or ""
    ).strip()
    load_local_env()
    user = os.environ.get("SMTP_USER", "").strip()
    auth = os.environ.get("SMTP_AUTH_CODE", "").strip()
    host = os.environ.get("SMTP_HOST", "smtp.sina.com").strip() or "smtp.sina.com"
    port = _smtp_port()
    allowed = smtp_whitelist()
    if not user or not auth:
        return _result(False, "未配置 SMTP，邮件未发送。")
    if not allowed:
        return _result(False, "未配置发信白名单，邮件未发送。")
    resolved = resolve_whitelist_recipient(to_raw, allowed)
    if not resolved:
        return _result(False, "收件人不在发信白名单中，邮件未发送。")
    if not body:
        return _result(False, "缺少邮件正文。请告诉我要写什么内容，不要编造。")
    if not subject:
        subject = body[:40]
    too_long = _too_long(subject, body)
    if too_long:
        return _result(False, too_long)
    if _blocked_body(body) or _blocked_body(subject):
        return _result(False, "邮件主题或正文含有敏感信息或注入内容，邮件未发送。")

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = resolved
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, auth)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return _result(False, "SMTP 认证失败，邮件未发送。")
    except (smtplib.SMTPConnectError, TimeoutError, OSError):
        return _result(False, "当前邮件服务繁忙，请稍后再试。")
    except smtplib.SMTPException:
        return _result(False, "发送失败，邮件未发送。")
    return _result(True, f"已发送邮件，主题：{subject}", to=resolved, subject=subject, body=body)


def smtp_whitelist() -> tuple[str, ...]:
    load_local_env()
    raw = os.environ.get("SMTP_WHITELIST", "")
    found: list[str] = []
    for item in re.split(r"[,;\s]+", raw):
        email = item.strip().casefold()
        if email and _EMAIL_RE.match(email) and email not in found:
            found.append(email)
    return tuple(found)


def resolve_whitelist_recipient(raw: str, allowed: tuple[str, ...] | None = None) -> str:
    allowed = allowed if allowed is not None else smtp_whitelist()
    if not allowed:
        return ""
    text = (raw or "").strip()
    if not text or _is_owner_alias(text):
        return allowed[0]
    lowered = text.casefold()
    if _EMAIL_RE.match(lowered):
        return lowered if lowered in allowed else ""
    return ""


def _is_owner_alias(text: str) -> bool:
    cleaned = (text or "").strip()
    return any(alias == cleaned or alias in cleaned for alias in _OWNER_ALIASES)


def _too_long(subject: str, body: str) -> str:
    if len(subject) > MAX_SUBJECT_CHARS:
        return f"邮件主题过长（>{MAX_SUBJECT_CHARS} 字），已拒绝发送。"
    if len(body) > MAX_BODY_CHARS:
        return f"邮件正文过长（>{MAX_BODY_CHARS} 字），已拒绝发送。"
    return ""


def _blocked_body(text: str) -> bool:
    lowered = (text or "").lower()
    if any(mark in lowered for mark in _INJECTION):
        return True
    return bool(_ID_CARD.search(text) or _BANK_CARD.search(text) or _SECRET.search(text))


def _smtp_port() -> int:
    raw = os.environ.get("SMTP_PORT", "465").strip()
    try:
        return int(raw)
    except ValueError:
        return 465


def _result(sent: bool, summary: str, **extra: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"summary": summary, "sent": sent}
    payload.update(extra)
    return payload


def _apply_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
