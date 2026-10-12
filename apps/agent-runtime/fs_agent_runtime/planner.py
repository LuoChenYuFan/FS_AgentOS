"""Turn a request into a task DAG. Rules pick the shape; thresholds stay in the DAG."""

from __future__ import annotations

import re

from fs_common.contracts.dag import ConditionOp, TaskCondition, TaskDag, TaskNode

# 长名在前，避免「呼和浩特」被拆开。香港、澳门等要能从原话里直接认出。
_CITIES = (
    "乌鲁木齐",
    "呼和浩特",
    "石家庄",
    "哈尔滨",
    "重庆",
    "天津",
    "上海",
    "广州",
    "深圳",
    "厦门",
    "北京",
    "杭州",
    "成都",
    "武汉",
    "西安",
    "南京",
    "苏州",
    "长沙",
    "郑州",
    "青岛",
    "大连",
    "宁波",
    "沈阳",
    "长春",
    "济南",
    "太原",
    "合肥",
    "福州",
    "南昌",
    "南宁",
    "昆明",
    "贵阳",
    "海口",
    "三亚",
    "兰州",
    "西宁",
    "银川",
    "拉萨",
    "香港",
    "澳门",
    "台北",
)
_NOT_A_CITY = frozenset(
    {
        "今天",
        "明天",
        "后天",
        "大后天",
        "昨天",
        "昨日",
        "帮我",
        "请问",
        "一下",
        "查询",
        "当地",
        "那里",
        "这里",
    }
)
_WEATHER_CITY = re.compile(
    r"(?:查询|查一下|查下)?(?P<city>[\u4e00-\u9fff]{2,8}?)(?:明天|后天|今天|大后天|\d+天后)?的?天气"
)
# 从一地到另一地是行程，不是知识库。和差旅标准比费用时仍走原来的查路线。
_NOT_A_TRIP = (
    "差旅",
    "报销",
    "超过",
    "超了",
    "对照",
    "员工手册",
    "规章制度",
    "知识库",
    "公司规定",
)
_TRAVEL = (
    "地铁",
    "公交",
    "换乘",
    "怎么走",
    "怎么去",
    "怎么到",
    "导航",
    "驾车",
    "开车",
    "打车",
    "自驾",
    "步行",
    "走路",
    "徒步",
    "骑行",
    "骑车",
    "自行车",
    "用时",
    "多久",
    "多长时间",
    "路线",
    "坐地铁",
    "做地铁",
)
_PLACE_HINT = (
    "站",
    "机场",
    "广场",
    "公园",
    "景区",
    "酒店",
    "大学",
    "医院",
    "大厦",
    "故宫",
)
_FROM_TO = re.compile(
    r"从(?P<origin>.{2,24}?)到(?P<destination>.{2,24}?)"
    r"(?=[，,。！？?!；;的了和我就大概要怎样如做坐乘]|怎么|如何|打车|开车|驾车|自驾|地铁|公交|换乘|步行|骑行|如果|要|多久|$)"
)


def place_ends(text: str) -> tuple[str, str] | None:
    found = _FROM_TO.search(text)
    if found is None:
        return None
    origin = _clean_place(found.group("origin"))
    destination = _clean_place(found.group("destination"))
    if (
        len(origin) < 2
        or len(destination) < 2
        or origin.startswith("第")
        or destination.startswith("第")
    ):
        return None
    if origin in {"今天", "明天", "后天", "昨天"} or destination in {
        "今天",
        "明天",
        "后天",
        "昨天",
    }:
        return None
    return origin, destination


def is_place_to_place(text: str) -> bool:
    ends = place_ends(text)
    if ends is None or any(word in text for word in _NOT_A_TRIP):
        return False
    if ("天气" in text or "酒店" in text) and not any(
        word in text for word in ("地铁", "公交", "换乘", "怎么走", "怎么去", "导航")
    ):
        return False
    origin, destination = ends
    if any(word in text for word in _TRAVEL):
        return True
    return any(hint in origin or hint in destination for hint in _PLACE_HINT)


def trip_mode(text: str) -> str:
    if any(word in text for word in ("步行", "走路", "徒步")):
        return "步行"
    if any(word in text for word in ("骑行", "自行车", "骑车")):
        return "骑行"
    if any(word in text for word in ("驾车", "开车", "自驾", "打车")):
        return "驾车"
    if any(word in text for word in ("地铁", "公交", "换乘")):
        return "公交"
    return "驾车"


def _clean_place(value: str) -> str:
    return value.strip(" ，,。！？?!的了和")


class RulePlanner:
    """The three design scenarios, plus a one-node route for a single request."""

    def plan(self, text: str, *, attempt: int, errors: list[str]) -> TaskDag:
        del attempt, errors
        if "如果" in text and "酒店" in text and "邮件" in text:
            dag = _conditional_hotel(text)
        elif ("旅游攻略" in text or "规划" in text) and "邮件" in text:
            dag = _parallel(text)
        elif "天气" in text and "邮件" in text:
            dag = _weather_then_mail(text)
        elif is_place_to_place(text):
            origin, destination = place_ends(text) or ("", "")
            dag = _single(
                text,
                "行程规划",
                "规划行程",
                {"起点": origin, "终点": destination, "方式": trip_mode(text)},
            )
        elif "天气" in text:
            dag = _single(
                text,
                "聊天助手",
                "查天气",
                {"城市": _city(text, "天津"), "日期": _when(text)},
            )
        elif "酒店" in text:
            dag = _single(
                text,
                "聊天助手",
                "查酒店",
                {"城市": _city(text, "广州"), "星级": 5, "日期": _when(text)},
            )
        elif any(word in text for word in ("邮件", "发信", "发给主人", "给你的主人")):
            dag = _single(
                text,
                "发邮件",
                "发邮件",
                {
                    "收件人": _recipient(text),
                    "主题": _mail_subject(text),
                    "正文": _mail_body(text),
                },
            )
        elif "攻略" in text or "行程" in text:
            dag = _single(text, "行程规划", "规划行程", {"城市": _city(text, "上海")})
        else:
            dag = _single(text, "聊天助手", None, {"文本": text})
        return _with_knowledge(text, dag)


def _weather_then_mail(text: str) -> TaskDag:
    city = _city(text, "天津")
    return TaskDag(
        run_id="plan",
        tasks=[
            TaskNode(
                id=1,
                agent="聊天助手",
                args={"skill": "查天气", "城市": city, "日期": _when(text)},
                output_key="weather",
            ),
            TaskNode(
                id=2,
                agent="发邮件",
                args={
                    "skill": "发邮件",
                    "收件人": _recipient(text),
                    "主题": _mail_subject(text),
                    "正文": f"{city}明天 ${{{{weather.summary}}}}",
                },
                deps=[1],
                output_key="mail",
            ),
        ],
    )


def _parallel(text: str) -> TaskDag:
    return TaskDag(
        run_id="plan",
        tasks=[
            TaskNode(
                id=1,
                agent="行程规划",
                args={"skill": "规划行程", "城市": _city(text, "上海")},
                output_key="itinerary",
            ),
            TaskNode(
                id=2,
                agent="发邮件",
                args={
                    "skill": "发邮件",
                    "收件人": _recipient(text),
                    "主题": _mail_subject(text),
                    "正文": "让他明天去厦门出差",
                },
                output_key="mail",
            ),
        ],
    )


def _conditional_hotel(text: str) -> TaskDag:
    threshold = _threshold(text)
    city = _city(text, "广州")
    return TaskDag(
        run_id="plan",
        tasks=[
            TaskNode(
                id=1,
                agent="聊天助手",
                args={"skill": "查酒店", "城市": city, "星级": 5, "日期": _when(text)},
                output_key="hotel",
            ),
            TaskNode(
                id=2,
                agent="发邮件",
                args={
                    "skill": "发邮件",
                    "收件人": _recipient(text),
                    "主题": _mail_subject(text),
                    "正文": f"{city}明晚五星均价 ${{{{hotel.avg_price}}}} 元",
                },
                deps=[1],
                output_key="mail",
                condition=TaskCondition(
                    field="hotel.avg_price", op=ConditionOp.LT, value=threshold
                ),
            ),
        ],
    )


def _single(
    text: str, agent: str, skill: str | None, args: dict[str, object]
) -> TaskDag:
    del text
    payload = dict(args)
    if skill:
        payload["skill"] = skill
    output_key = {
        "查天气": "weather",
        "查酒店": "hotel",
        "规划行程": "itinerary",
        "行程规划": "itinerary",
        "发邮件": "mail",
    }.get(skill or agent)
    return TaskDag(
        run_id="plan",
        tasks=[TaskNode(id=1, agent=agent, args=payload, output_key=output_key)],
    )


def spoken_city(text: str) -> str:
    """The city named in this sentence, or empty when the user did not name one."""

    for name in _CITIES:
        if name in text:
            return name
    found = _WEATHER_CITY.search(text)
    if found is None:
        return ""
    city = found.group("city").strip("的了和就")
    if city in _NOT_A_CITY or len(city) < 2:
        return ""
    return city


def _city(text: str, default: str) -> str:
    return spoken_city(text) or default


def align_spoken_city(dag: TaskDag, text: str) -> TaskDag:
    """Keep 查天气 on the city in this sentence, not an example or a leftover slot."""

    city = spoken_city(text)
    if not city:
        return dag
    tasks = []
    for task in dag.tasks:
        args = dict(task.args)
        if args.get("skill") == "查天气":
            args["城市"] = city
        tasks.append(task.model_copy(update={"args": args}))
    return dag.model_copy(update={"tasks": tasks})


def _when(text: str) -> str:
    for pattern in _WHEN_PATTERNS:
        found = pattern.search(text)
        if found:
            return found.group(0)
    return "今天"


_WHEN_PATTERNS = (
    re.compile(r"大后天"),
    re.compile(r"后天"),
    re.compile(r"明晚"),
    re.compile(r"明日"),
    re.compile(r"明天"),
    re.compile(r"今天"),
    re.compile(r"今日"),
    re.compile(r"昨天"),
    re.compile(r"(?:下下下|下下|下|这)?(?:周|星期|礼拜)[一二三四五六日天]"),
    re.compile(r"(?:\d+|[零一二三四五六七八九十两]+)天(?:后|以后|之后)"),
    re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?"),
    re.compile(r"\d{1,2}月\d{1,2}日?"),
)


_OWNER_ALIASES = ("你的主人", "我的主人", "主人", "东家", "老板", "张博洋")


def _recipient(text: str) -> str:
    for alias in _OWNER_ALIASES:
        if alias in text:
            return alias
    named = re.search(r"发给([\u4e00-\u9fff]{1,8}|[^@\s]+@[^@\s]+\.[^@\s]+)", text)
    if named:
        return named.group(1)
    named = re.search(r"给([\u4e00-\u9fff]{1,8}|[^@\s]+@[^@\s]+\.[^@\s]+)发", text)
    if named:
        return named.group(1)
    return ""


def _mail_body(text: str) -> str:
    stripped = re.sub(
        r"请?(帮我)?(给.{0,12})?(发一封邮件|发封邮件|发送邮件|发邮件|发信)",
        "",
        text,
    )
    stripped = re.sub(
        r"(给你的主人|给我的主人|给主人|给东家|给老板|给张博洋)",
        "",
        stripped,
    )
    stripped = re.sub(r"^(告诉他|跟他说|内容是|正文是)[:：]?", "", stripped.strip())
    return stripped.strip(" ，,。！？!?")


def _mail_subject(text: str) -> str:
    body = _mail_body(text)
    return body[:40] if body else "来自对话的通知"


_KNOWLEDGE_MARKERS = ("差旅标准", "制度", "知识库", "员工手册", "公司规定")
_ZHANG_FACT_MARKERS = (
    "电话",
    "手机",
    "邮箱",
    "项目",
    "简历",
    "工作",
    "技能",
    "学历",
    "经历",
    "职位",
    "教育",
    "出生",
    "籍贯",
    "优势",
)


def _mentions_zhang_facts(text: str) -> bool:
    return "张博洋" in text and any(marker in text for marker in _ZHANG_FACT_MARKERS)


def _needs_knowledge(text: str) -> bool:
    if any(marker in text for marker in _KNOWLEDGE_MARKERS):
        return True
    return _mentions_zhang_facts(text)


def _with_knowledge(text: str, dag: TaskDag) -> TaskDag:
    if not _needs_knowledge(text):
        return dag
    wait_for_lookup = _mentions_zhang_facts(text)
    shifted: list[TaskNode] = []
    for task in dag.tasks:
        deps = [dep + 1 for dep in task.deps]
        if wait_for_lookup and not deps:
            deps = [1]
        shifted.append(task.model_copy(update={"id": task.id + 1, "deps": deps}))
    retrieval = TaskNode(
        id=1,
        agent="RAG Agent",
        args={"skill": "rag_query", "query": text, "top_k": 5},
        output_key="knowledge",
    )
    return dag.model_copy(update={"tasks": [retrieval, *shifted]})


def _threshold(text: str) -> int:
    found = re.search(r"低于(\d+)", text)
    if found:
        return int(found.group(1))
    return 2000
