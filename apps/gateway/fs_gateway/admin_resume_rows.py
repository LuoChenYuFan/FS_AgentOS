"""Build resume-library table rows from platform tenants + resume CMS."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

_AVATAR_GRADIENTS = (
    "linear-gradient(135deg,#3b5bfd,#7c5cff)",
    "linear-gradient(135deg,#16a34a,#4ade80)",
    "linear-gradient(135deg,#f59e0b,#fbbf24)",
    "linear-gradient(135deg,#ec4899,#f472b6)",
    "linear-gradient(135deg,#0ea5e9,#38bdf8)",
    "linear-gradient(135deg,#7c5cff,#a78bfa)",
    "linear-gradient(135deg,#14b8a6,#5eead4)",
    "linear-gradient(135deg,#ef4444,#fb7185)",
)

_COMPLETE_KEYS = (
    "BasicInfo",
    "Skills",
    "WorkExperience",
    "Projects",
    "Strengths",
)


def build_resume_library_rows(
    platform: Any, *, status: str = "active"
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tenants = [
        tenant
        for tenant in platform.list_tenants()
        if tenant.slug != "platform" and tenant.status == status
    ]
    for index, tenant in enumerate(tenants):
        profile = platform.get_profile(tenant.id)
        sections = {
            item.section_key: item for item in platform.list_sections(tenant.id)
        }
        basic = sections.get("BasicInfo")
        fields = _basic_fields(basic)
        name = (
            _field_value(fields, nodes=("Name",), labels=("姓名",))
            or (tenant.assistant_owner or "").strip()
            or tenant.display_name
            or tenant.slug
        )
        age = _field_value(fields, nodes=("Age", "age"), labels=("年龄",))
        phone = _field_value(
            fields,
            nodes=("Contact phone number", "Phone", "phone"),
            labels=("联系电话", "电话", "手机"),
        )
        years = _years_of_experience(sections.get("WorkExperience"))
        meta_parts: list[str] = []
        if age:
            meta_parts.append(age if "岁" in age else f"{age}岁")
        if years is not None:
            meta_parts.append("应届生" if years <= 0 else f"{years}年经验")
        job_intent = _clean_headline(profile.headline if profile else "")
        complete = all(_section_filled(sections.get(key), key) for key in _COMPLETE_KEYS)
        rows.append(
            {
                "slug": tenant.slug,
                "name": name,
                "avatar_char": name[:1] or "用",
                "avatar_bg": _AVATAR_GRADIENTS[index % len(_AVATAR_GRADIENTS)],
                "meta": " · ".join(meta_parts) if meta_parts else f"/{tenant.slug}",
                "job_intent": job_intent or "—",
                "phone_masked": _mask_phone(phone),
                "registered_at": (tenant.created_at or "").strip() or "—",
                "status": "已完善" if complete else "待完善",
                "complete": complete,
                "detail_url": f"/admin/tenants/{tenant.slug}",
            }
        )
    return rows


def resume_library_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    incomplete = sum(1 for row in rows if not row.get("complete"))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_count = sum(
        1 for row in rows if str(row.get("registered_at") or "").startswith(today)
    )
    rate = f"{(total - incomplete) / total * 100:.1f}%" if total else "0%"
    return {
        "total": total,
        "today": today_count,
        "incomplete": incomplete,
        "complete_rate": rate,
    }


def basic_fields(section: Any) -> list[dict[str, Any]]:
    return _basic_fields(section)


def field_value(
    fields: list[dict[str, Any]],
    *,
    nodes: tuple[str, ...],
    labels: tuple[str, ...],
) -> str:
    return _field_value(fields, nodes=nodes, labels=labels)


def clean_headline(headline: str) -> str:
    return _clean_headline(headline)


def section_filled(section: Any, key: str) -> bool:
    return _section_filled(section, key)


def _basic_fields(section: Any) -> list[dict[str, Any]]:
    if section is None:
        return []
    content = section.content if isinstance(section.content, dict) else {}
    fields = content.get("fields") or []
    return [item for item in fields if isinstance(item, dict)]


def _field_value(
    fields: list[dict[str, Any]],
    *,
    nodes: tuple[str, ...],
    labels: tuple[str, ...],
) -> str:
    node_set = {item.lower() for item in nodes}
    for item in fields:
        node = str(item.get("node") or "").strip().lower()
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if not value:
            continue
        if node in node_set:
            return value
        if any(token in label for token in labels):
            return value
    return ""


def _clean_headline(headline: str) -> str:
    text = str(headline or "").strip()
    text = re.sub(r"^求职意向\s*[:：]\s*", "", text)
    return text.strip()


def _mask_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    if len(digits) >= 7:
        return f"{digits[:3]}****{digits[-4:]}"
    return phone.strip() or "—"


def _years_of_experience(section: Any) -> int | None:
    if section is None:
        return None
    content = section.content if isinstance(section.content, dict) else {}
    cards = content.get("cards") or []
    years: list[int] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        blob = f"{card.get('title') or ''} {card.get('body') or ''}"
        years.extend(int(match) for match in re.findall(r"(20\d{2}|19\d{2})", blob))
    if not years:
        return None
    current = datetime.now(timezone.utc).year
    return max(0, current - min(years))


def _section_filled(section: Any, key: str) -> bool:
    if section is None:
        return False
    content = section.content if isinstance(section.content, dict) else {}
    if key == "BasicInfo":
        fields = content.get("fields") or []
        return any(
            str(item.get("value") or "").strip()
            for item in fields
            if isinstance(item, dict)
        )
    if key in ("Skills", "Strengths"):
        return bool(str(content.get("body") or "").strip())
    if key in ("WorkExperience", "Projects"):
        cards = content.get("cards") or []
        return any(
            str(item.get("title") or "").strip() or str(item.get("body") or "").strip()
            for item in cards
            if isinstance(item, dict)
        )
    return False
