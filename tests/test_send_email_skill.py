"""发邮件只投递 SMTP_WHITELIST 中的地址。"""

from __future__ import annotations

from fs_agent_runtime.planner import RulePlanner
from fs_agent_runtime.skills.loader import run_skill, skill_module


class _FakeSMTP:
    sent: list[tuple[str, str]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def login(self, user: str, password: str) -> None:
        del user, password

    def send_message(self, message: object) -> None:
        self.sent.append((str(message["To"]), str(message["Subject"])))


def test_whitelist_address_is_sent_and_others_are_rejected(monkeypatch) -> None:
    mail = skill_module("发邮件")
    monkeypatch.setenv("SMTP_USER", "sender@example.com")
    monkeypatch.setenv("SMTP_AUTH_CODE", "secret")
    monkeypatch.setenv("SMTP_WHITELIST", "owner@example.com")
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", _FakeSMTP)
    _FakeSMTP.sent = []
    allowed = mail.run({"收件人": "主人", "正文": "我明天调休一天"})
    assert allowed["sent"] is True
    assert "已发送" in allowed["summary"]
    assert _FakeSMTP.sent[0][0] == "owner@example.com"

    denied = run_skill("发邮件", {"收件人": "attacker@evil.com", "正文": "hello"})
    assert denied["sent"] is False
    assert "白名单" in denied["summary"]
    assert len(_FakeSMTP.sent) == 1


def test_owner_aliases_resolve_to_the_whitelist() -> None:
    mail = skill_module("发邮件")
    allowed = ("owner@example.com",)
    assert mail.resolve_whitelist_recipient("主人", allowed) == "owner@example.com"
    assert mail.resolve_whitelist_recipient("你的主人", allowed) == "owner@example.com"
    assert mail.resolve_whitelist_recipient("owner@example.com", allowed) == "owner@example.com"
    assert mail.resolve_whitelist_recipient("other@example.com", allowed) == ""
    assert mail.resolve_whitelist_recipient("张三", allowed) == ""


def test_planner_routes_an_owner_mail_to_the_mail_agent() -> None:
    dag = RulePlanner().plan("帮我给你的主人发一封邮件，内容是下午三点开会", attempt=1, errors=[])
    assert dag.tasks[0].agent == "发邮件"
    assert dag.tasks[0].args["skill"] == "发邮件"
    assert "开会" in str(dag.tasks[0].args["正文"])
