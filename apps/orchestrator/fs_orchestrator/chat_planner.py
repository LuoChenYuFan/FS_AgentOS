"""Ask the chat model for a task DAG. Arithmetic stays in the scheduler."""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Protocol

from fs_agent_runtime.planner import RulePlanner, align_spoken_city, is_place_to_place
from fs_agent_runtime.registry import Catalog, load_catalog
from fs_common.contracts.dag import TaskDag
from pydantic import ValidationError

_SYSTEM = """你是任务计划器。只返回 JSON，不要解释。
格式：{"tasks":[{"id":1,"agent":"聊天助手","args":{"skill":"查天气","城市":"上海","日期":"明天"},"deps":[],"output_key":"weather","condition":null}]}
规则：
- agent 只能是：聊天助手、行程规划、发邮件、RAG Agent、计算。
- 聊天助手的 skill 只能是查天气、查路线、查酒店。查酒店时 args 写城市、星级、日期，output_key 用 hotel。查天气 output_key 用 weather。查路线 args 只能写起点、终点、方式，不要写出发地或目的地，output_key 用 route。路线费用字段是 route.fare。
- 用户提到天气或酒店时，对应的查天气、查酒店必须出现。少了会被打回重做。
- 用户问从一地到另一地、地铁或公交换乘、怎么走、要多久、多少钱时，agent 为行程规划，skill 为规划行程，output_key 为 itinerary。args 写起点、终点、方式（地铁、公交、换乘写公交；打车、开车写驾车）。最近对话里已经有起点和终点时，接着改成打车、开车或步行仍用规划行程，不要用查路线，也不要走 RAG Agent。
- 最近对话只用来理解“明天呢、回去、他”这类指代。本句已经写明的地点、日期、收件人以本句为准。
- 只有在和差旅、报销标准比较费用时，路线、乘车、打车才用聊天助手的查路线。
- 用户要规划行程、旅游攻略或一日游时，agent 为行程规划，skill 为规划行程，output_key 为 itinerary。args 写城市；有起点和终点时再写起点、终点、方式。
- 查天气的城市必须用用户原话里的城市，不要套用格式示例里的上海。日期按用户原话填写，例如今天、明天、后天、大后天、3天后、10月8日。不要把后天改成今天。超过 10 天也原样传入，不要改短。
- 需要公司制度、差旅、报销标准时，加一步 agent 为 RAG Agent，skill 为 rag_query，args.query 写要查的问题，output_key 为 knowledge。
- 给张博洋发邮件、查天气、查酒店、查路线时，按对应的办事步骤来，不要因为出现张博洋就加 RAG Agent。
- 既要查张博洋的电话、邮箱、项目、简历、工作、技能、学历或经历，又要发邮件或查天气、酒店、路线时，先加 RAG Agent，再接后面的办事步骤。
- 从制度原文里取金额：agent 为计算，skill 为 amount_from_text，args.text 用 ${{knowledge.chunks}}，output_key 为 cap，deps 包含检索那一步。
- 用户要把费用和差旅标准比较、或询问超了多少时，必须再加 subtract，output_key 为 gap。args.left 和 args.right 用 ${{输出.字段}}。酒店用 hotel.avg_price，路线用 route.fare，标准金额用 cap.amount。不要自己计算数字。
- 发邮件的 agent 为发邮件，skill 必须是发邮件，output_key 用 mail。args 写收件人、主题、正文。给主人、东家、老板发信时收件人写主人。正文用用户要传达的内容，不要编造。需要条件时 condition 为 {"field":"hotel.avg_price","op":"<","value":3000}。op 只能是 <、>、==、in。
- 互不依赖的步骤 deps 用空列表，让它们同时做。
"""


class TextChat(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str: ...


_HISTORY: ContextVar[tuple[dict[str, str], ...]] = ContextVar("dialogue_history", default=())


def bind_history(turns: list[dict[str, str]]) -> object:
    kept = tuple(turns[-8:])
    return _HISTORY.set(kept)


def reset_history(token: object) -> None:
    _HISTORY.reset(token)  # type: ignore[arg-type]


class ChatPlanner:
    def __init__(self, chat: TextChat, catalog: Catalog | None = None) -> None:
        self._chat = chat
        self._rules = RulePlanner()
        self._catalog = catalog

    def plan(self, text: str, *, attempt: int, errors: list[str]) -> TaskDag:
        if is_place_to_place(text):
            return self._rules.plan(text, attempt=attempt, errors=errors)
        catalog = self._catalog or load_catalog()
        self._catalog = catalog
        feedback = ""
        if errors:
            feedback = "上次任务单未通过校验：" + "；".join(errors) + "。请只返回修正后的 JSON。"
        try:
            raw = self._chat.complete(
                [
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": f"{_format_history(_HISTORY.get())}{feedback}\n用户原话：{text}".strip(),
                    },
                ]
            )
        except (RuntimeError, ValueError, TypeError):
            return self._rules.plan(text, attempt=attempt, errors=errors)
        try:
            return align_spoken_city(_parse_dag(raw), text)
        except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
            if attempt >= 3:
                return self._rules.plan(text, attempt=attempt, errors=errors)
            raise ValueError(f"任务单无法解析：{exc}") from exc


def _format_history(turns: tuple[dict[str, str], ...]) -> str:
    if not turns:
        return ""
    lines: list[str] = []
    for turn in turns:
        role = "用户" if turn.get("role") == "user" else "助手"
        lines.append(f"{role}：{turn.get('text', '')}")
    return "最近对话：\n" + "\n".join(lines) + "\n"


def _parse_dag(raw: str) -> TaskDag:
    text = raw.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model did not return an object")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise TypeError("model did not return an object")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise TypeError("tasks must be a list")
    for task in tasks:
        if isinstance(task, dict) and isinstance(task.get("id"), str) and task["id"].isdigit():
            task["id"] = int(task["id"])
        if isinstance(task, dict):
            deps = task.get("deps") or []
            task["deps"] = [int(dep) if isinstance(dep, str) and dep.isdigit() else dep for dep in deps]
            if task.get("condition") is None:
                task["condition"] = None
    return TaskDag.model_validate({"run_id": "plan", "tasks": tasks})
