"""Run one agent. Cross-agent order stays in the scheduler."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fs_agent_runtime.mail_draft import prepare_mail_draft
from fs_agent_runtime.registry import AgentSpec
from fs_agent_runtime.skills.loader import run_skill

WeatherFn = Callable[[dict[str, Any]], dict[str, Any]]
ItineraryFn = Callable[[dict[str, Any]], dict[str, Any]]
MailFn = Callable[[dict[str, Any]], dict[str, Any]]


class AgentRunner:
    def __init__(
        self,
        *,
        hotel_price: float = 1850,
        weather: WeatherFn | None = None,
        itinerary: ItineraryFn | None = None,
        mail: MailFn | None = None,
    ) -> None:
        self.hotel_price = hotel_price
        self.seen: list[dict[str, Any]] = []
        self._weather = weather
        self._itinerary = itinerary
        self._mail = mail

    def execute(
        self, *, agent: AgentSpec, args: dict[str, Any], skill: str | None
    ) -> dict[str, Any]:
        self.seen.append(dict(args))
        if skill == "发邮件" or agent.name == "发邮件":
            return prepare_mail_draft(args)
        if agent.side_effect == "irreversible":
            return prepare_mail_draft(args)
        if skill == "查天气":
            if self._weather is not None:
                return self._weather(args)
            return run_skill("查天气", args)
        if skill == "查酒店":
            found = dict(run_skill("查酒店", args))
            found["avg_price"] = float(self.hotel_price)
            return found
        if skill == "查路线":
            return run_skill("查路线", args)
        if skill == "规划行程" or agent.name == "行程规划":
            if self._itinerary is not None:
                return self._itinerary(args)
            return run_skill("规划行程", args)
        return {"answer": str(args.get("文本") or "")}
