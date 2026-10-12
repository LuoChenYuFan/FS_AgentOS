"""Default resume section keys and empty JSON shapes for Unity PanelIds."""

from __future__ import annotations

from typing import Any

# Unity BasicInfo 面板上的固定行。管理页只编辑 value，node 给场景物体用。
BASIC_INFO_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Name", "姓名", ("姓名",)),
    ("Place of origin", "籍贯", ("籍贯",)),
    ("Educational background", "学历", ("学历",)),
    ("Contact phone number", "联系电话", ("联系电话", "电话", "手机")),
    ("Email", "邮箱", ("邮箱", "邮件")),
)


def normalize_basic_info(content: dict[str, Any] | None) -> dict[str, Any]:
    """Keep the five fixed rows. Match old free-form labels so values are not dropped."""
    raw = content.get("fields") if isinstance(content, dict) else None
    rows = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    fields: list[dict[str, str]] = []
    for node, label, aliases in BASIC_INFO_FIELDS:
        value = ""
        for row in rows:
            row_node = str(row.get("node") or "").strip()
            row_label = str(row.get("label") or "")
            if row_node == node or any(alias in row_label for alias in aliases):
                value = str(row.get("value") or "").strip()
                break
        fields.append({"node": node, "label": label, "value": value})
    return {"fields": fields}


def public_section_content(section_key: str, content: dict[str, Any]) -> dict[str, Any]:
    if section_key == "BasicInfo":
        return normalize_basic_info(content)
    return content


DEFAULT_SECTION_SPECS: list[dict[str, Any]] = [
    {
        "section_key": "BasicInfo",
        "title": "基本信息",
        "sort_order": 0,
        "content": {"fields": []},
    },
    {
        "section_key": "Skills",
        "title": "专业技能",
        "sort_order": 1,
        "content": {"body": ""},
    },
    {
        "section_key": "WorkExperience",
        "title": "工作经历",
        "sort_order": 2,
        "content": {"cards": []},
    },
    {
        "section_key": "Projects",
        "title": "项目经历",
        "sort_order": 3,
        "content": {"cards": []},
    },
    {
        "section_key": "Education",
        "title": "教育经历",
        "sort_order": 4,
        "content": {"items": []},
    },
    {
        "section_key": "TechIntro",
        "title": "简历技术介绍",
        "sort_order": 5,
        "content": {"cards": []},
    },
    {
        "section_key": "Strengths",
        "title": "个人优势",
        "sort_order": 6,
        "content": {"body": ""},
    },
]


def empty_section_content(section_key: str) -> dict[str, Any]:
    for spec in DEFAULT_SECTION_SPECS:
        if spec["section_key"] == section_key:
            return dict(spec["content"])
    return {"body": ""}
