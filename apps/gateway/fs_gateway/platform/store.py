"""In-memory and PostgreSQL stores for tenants, users, and resume CMS."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from fs_common import new_id
from fs_gateway.auth.passwords import hash_password, verify_password
from fs_gateway.platform.resume_schema import DEFAULT_SECTION_SPECS, public_section_content

_SLUG = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$")
DEFAULT_ASSISTANT_CAPABILITIES = ("查天气", "查路线", "查简历信息", "发邮件")


@dataclass(slots=True)
class Tenant:
    id: str
    slug: str
    display_name: str
    status: str = "active"
    assistant_owner: str = ""
    assistant_capabilities: list[str] = field(
        default_factory=lambda: list(DEFAULT_ASSISTANT_CAPABILITIES)
    )
    created_at: str = ""


def normalize_capabilities(raw: Any) -> list[str]:
    if isinstance(raw, str):
        parts = [piece.strip() for piece in re.split(r"[,，、\n]+", raw) if piece.strip()]
        return parts or list(DEFAULT_ASSISTANT_CAPABILITIES)
    if isinstance(raw, (list, tuple)):
        parts = [str(item).strip() for item in raw if str(item).strip()]
        return parts or list(DEFAULT_ASSISTANT_CAPABILITIES)
    return list(DEFAULT_ASSISTANT_CAPABILITIES)


def assistant_persona(tenant: Tenant | None) -> dict[str, str | list[str]]:
    """Build chat/RAG copy from tenant rows. Falls back when the tenant is missing."""
    owner = ""
    capabilities = list(DEFAULT_ASSISTANT_CAPABILITIES)
    if tenant is not None:
        owner = (tenant.assistant_owner or tenant.display_name or "").strip()
        capabilities = normalize_capabilities(tenant.assistant_capabilities)
    if not owner:
        owner = "主人"
    caps_text = "、".join(capabilities)
    identity = f"我是{owner}的AI小助理"
    return {
        "owner": owner,
        "capabilities": capabilities,
        "identity": identity,
        "fallback": f"我可以帮你{caps_text}。你直接说要办的事就行。",
        "chat_system": (
            f"你是{owner}的AI小助理。用户在闲聊，或在问你能做什么。"
            f"有人问你是谁，只回答：{identity}。"
            "直接用中文回答，不要去查知识库。"
            f"你能办的事有：{caps_text}。"
        ),
        "rag_system": (
            f"你是{owner}的AI小助理。只根据给出的资料回答用户的问题。"
            "资料里没有的内容不要编造。"
            "用简短的中文直接回答，电话、邮箱和数字保持资料中的原文。"
            "不要提到切片、检索或资料编号。"
        ),
    }


def _format_created_at(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OverflowError):
            pass
    text = str(value).replace("T", " ")
    return text[:16] if len(text) >= 16 else text


def _tenant_from_row(row: dict[str, Any]) -> Tenant:
    return Tenant(
        id=str(row["id"]),
        slug=str(row["slug"]),
        display_name=str(row["display_name"]),
        status=str(row.get("status") or "active"),
        assistant_owner=str(row.get("assistant_owner") or ""),
        assistant_capabilities=normalize_capabilities(row.get("assistant_capabilities")),
        created_at=_format_created_at(row.get("created_at")),
    )


@dataclass(slots=True)
class StoredUser:
    user_id: str
    tenant_id: str
    username: str
    password_hash: str
    scopes: list[str]
    is_platform_admin: bool = False
    enabled: bool = True


@dataclass(slots=True)
class ResumeProfile:
    tenant_id: str
    published: bool
    headline: str = ""


@dataclass(slots=True)
class ResumeSection:
    id: str
    tenant_id: str
    section_key: str
    title: str
    enabled: bool
    sort_order: int
    content: dict[str, Any]


class PlatformStore(Protocol):
    def list_tenants(self) -> list[Tenant]: ...

    def get_tenant(self, tenant_id: str) -> Tenant | None: ...

    def get_tenant_by_slug(self, slug: str) -> Tenant | None: ...

    def create_tenant(
        self,
        *,
        tenant_id: str,
        slug: str,
        display_name: str,
        ai_username: str,
        ai_password: str,
        ai_user_id: str | None = None,
        ai_scopes: list[str] | None = None,
        password_iterations: int = 20_000,
    ) -> Tenant: ...

    def set_tenant_status(self, tenant_id: str, status: str) -> Tenant: ...

    def delete_tenant(self, tenant_id: str) -> None: ...

    def set_tenant_display_name(self, tenant_id: str, display_name: str) -> Tenant: ...

    def update_tenant(
        self,
        tenant_id: str,
        *,
        display_name: str | None = None,
        assistant_owner: str | None = None,
        assistant_capabilities: list[str] | None = None,
    ) -> Tenant: ...

    def upsert_user(
        self,
        *,
        user_id: str,
        tenant_id: str,
        username: str,
        password: str | None = None,
        password_hash: str | None = None,
        scopes: list[str],
        is_platform_admin: bool = False,
        enabled: bool = True,
        password_iterations: int = 20_000,
    ) -> StoredUser: ...

    def authenticate(self, username: str, password: str) -> StoredUser | None: ...

    def get_user(self, username: str) -> StoredUser | None: ...

    def get_profile(self, tenant_id: str) -> ResumeProfile | None: ...

    def set_published(self, tenant_id: str, published: bool, headline: str | None = None) -> ResumeProfile: ...

    def list_sections(self, tenant_id: str, *, enabled_only: bool = False) -> list[ResumeSection]: ...

    def get_section(self, tenant_id: str, section_key: str) -> ResumeSection | None: ...

    def upsert_section(
        self,
        *,
        tenant_id: str,
        section_key: str,
        title: str,
        enabled: bool,
        sort_order: int,
        content: dict[str, Any],
        section_id: str | None = None,
    ) -> ResumeSection: ...

    def public_resume(self, slug: str) -> dict[str, Any] | None: ...


def validate_slug(slug: str) -> str:
    cleaned = slug.strip().lower()
    if not _SLUG.match(cleaned):
        raise ValueError("slug must be lowercase letters, digits, and hyphens")
    return cleaned


def seed_default_sections(store: PlatformStore, tenant_id: str, seed: dict[str, Any] | None = None) -> None:
    by_key = {
        str(item["section_key"]): item
        for item in (seed or {}).get("sections", [])
        if isinstance(item, dict) and item.get("section_key")
    }
    for spec in DEFAULT_SECTION_SPECS:
        key = str(spec["section_key"])
        override = by_key.get(key, {})
        store.upsert_section(
            tenant_id=tenant_id,
            section_key=key,
            title=str(override.get("title") or spec["title"]),
            enabled=bool(override.get("enabled", True)),
            sort_order=int(override.get("sort_order", spec["sort_order"])),
            content=dict(override.get("content") or spec["content"]),
        )
    headline = str((seed or {}).get("headline") or "")
    store.set_published(tenant_id, published=True, headline=headline)


def refresh_skills_section_from_seed(
    store: PlatformStore,
    tenant_id: str,
    seed: dict[str, Any],
) -> bool:
    """Migrate Skills from legacy items[] to body when PostgreSQL still has the old shape."""
    seed_skills = next(
        (
            item
            for item in seed.get("sections", [])
            if isinstance(item, dict) and item.get("section_key") == "Skills"
        ),
        None,
    )
    if seed_skills is None:
        return False
    seed_content = seed_skills.get("content")
    if not isinstance(seed_content, dict):
        return False
    seed_body = str(seed_content.get("body") or "").strip()
    if not seed_body:
        return False

    current = store.get_section(tenant_id, "Skills")
    if current is None:
        store.upsert_section(
            tenant_id=tenant_id,
            section_key="Skills",
            title=str(seed_skills.get("title") or "专业技能"),
            enabled=bool(seed_skills.get("enabled", True)),
            sort_order=int(seed_skills.get("sort_order", 1)),
            content=dict(seed_content),
        )
        return True

    content = current.content if isinstance(current.content, dict) else {}
    body = str(content.get("body") or "").strip()
    has_items = isinstance(content.get("items"), list) and bool(content.get("items"))
    # Only rewrite when still on items[] or body is empty; keep admin edits once body exists.
    if body and not has_items:
        return False

    store.upsert_section(
        tenant_id=tenant_id,
        section_key="Skills",
        title=str(seed_skills.get("title") or current.title or "专业技能"),
        enabled=bool(seed_skills.get("enabled", current.enabled)),
        sort_order=int(seed_skills.get("sort_order", current.sort_order)),
        content=dict(seed_content),
        section_id=current.id,
    )
    return True


def _is_placeholder_work_cards(cards: Any) -> bool:
    if not isinstance(cards, list) or not cards:
        return True
    if len(cards) != 1 or not isinstance(cards[0], dict):
        return False
    title = str(cards[0].get("title") or "")
    return "AI 应用开发" in title


def refresh_work_experience_section_from_seed(
    store: PlatformStore,
    tenant_id: str,
    seed: dict[str, Any],
) -> bool:
    """Replace empty/placeholder WorkExperience cards with seed content."""
    seed_section = next(
        (
            item
            for item in seed.get("sections", [])
            if isinstance(item, dict) and item.get("section_key") == "WorkExperience"
        ),
        None,
    )
    if seed_section is None:
        return False
    seed_content = seed_section.get("content")
    if not isinstance(seed_content, dict):
        return False
    seed_cards = seed_content.get("cards")
    if not isinstance(seed_cards, list) or not seed_cards:
        return False

    current = store.get_section(tenant_id, "WorkExperience")
    if current is None:
        store.upsert_section(
            tenant_id=tenant_id,
            section_key="WorkExperience",
            title=str(seed_section.get("title") or "工作经历"),
            enabled=bool(seed_section.get("enabled", True)),
            sort_order=int(seed_section.get("sort_order", 2)),
            content=dict(seed_content),
        )
        return True

    content = current.content if isinstance(current.content, dict) else {}
    if not _is_placeholder_work_cards(content.get("cards")):
        return False

    store.upsert_section(
        tenant_id=tenant_id,
        section_key="WorkExperience",
        title=str(seed_section.get("title") or current.title or "工作经历"),
        enabled=bool(seed_section.get("enabled", current.enabled)),
        sort_order=int(seed_section.get("sort_order", current.sort_order)),
        content=dict(seed_content),
        section_id=current.id,
    )
    return True


def _is_placeholder_project_cards(cards: Any) -> bool:
    if not isinstance(cards, list) or not cards:
        return True
    if len(cards) != 1 or not isinstance(cards[0], dict):
        return False
    title = str(cards[0].get("title") or "")
    return title.strip() in {"FS_AgentOS", "项目一", "项目1"}


def refresh_projects_section_from_seed(
    store: PlatformStore,
    tenant_id: str,
    seed: dict[str, Any],
) -> bool:
    """Replace empty/placeholder Projects cards with seed content."""
    seed_section = next(
        (
            item
            for item in seed.get("sections", [])
            if isinstance(item, dict) and item.get("section_key") == "Projects"
        ),
        None,
    )
    if seed_section is None:
        return False
    seed_content = seed_section.get("content")
    if not isinstance(seed_content, dict):
        return False
    seed_cards = seed_content.get("cards")
    if not isinstance(seed_cards, list) or not seed_cards:
        return False

    current = store.get_section(tenant_id, "Projects")
    if current is None:
        store.upsert_section(
            tenant_id=tenant_id,
            section_key="Projects",
            title=str(seed_section.get("title") or "项目经历"),
            enabled=bool(seed_section.get("enabled", True)),
            sort_order=int(seed_section.get("sort_order", 3)),
            content=dict(seed_content),
        )
        return True

    content = current.content if isinstance(current.content, dict) else {}
    if not _is_placeholder_project_cards(content.get("cards")):
        return False

    store.upsert_section(
        tenant_id=tenant_id,
        section_key="Projects",
        title=str(seed_section.get("title") or current.title or "项目经历"),
        enabled=bool(seed_section.get("enabled", current.enabled)),
        sort_order=int(seed_section.get("sort_order", current.sort_order)),
        content=dict(seed_content),
        section_id=current.id,
    )
    return True


def _is_placeholder_education_items(items: Any) -> bool:
    if not isinstance(items, list) or not items:
        return True
    if len(items) != 1 or not isinstance(items[0], dict):
        return False
    line = str(items[0].get("line") or "")
    return "计算机相关专业" in line


def refresh_education_section_from_seed(
    store: PlatformStore,
    tenant_id: str,
    seed: dict[str, Any],
) -> bool:
    """Replace empty/placeholder Education items with seed content."""
    seed_section = next(
        (
            item
            for item in seed.get("sections", [])
            if isinstance(item, dict) and item.get("section_key") == "Education"
        ),
        None,
    )
    if seed_section is None:
        return False
    seed_content = seed_section.get("content")
    if not isinstance(seed_content, dict):
        return False
    seed_items = seed_content.get("items")
    if not isinstance(seed_items, list) or not seed_items:
        return False

    current = store.get_section(tenant_id, "Education")
    if current is None:
        store.upsert_section(
            tenant_id=tenant_id,
            section_key="Education",
            title=str(seed_section.get("title") or "教育经历"),
            enabled=bool(seed_section.get("enabled", True)),
            sort_order=int(seed_section.get("sort_order", 4)),
            content=dict(seed_content),
        )
        return True

    content = current.content if isinstance(current.content, dict) else {}
    if not _is_placeholder_education_items(content.get("items")):
        return False

    store.upsert_section(
        tenant_id=tenant_id,
        section_key="Education",
        title=str(seed_section.get("title") or current.title or "教育经历"),
        enabled=bool(seed_section.get("enabled", current.enabled)),
        sort_order=int(seed_section.get("sort_order", current.sort_order)),
        content=dict(seed_content),
        section_id=current.id,
    )
    return True


def _is_placeholder_tech_intro_cards(cards: Any) -> bool:
    if not isinstance(cards, list) or not cards:
        return True
    for card in cards:
        if not isinstance(card, dict):
            return True
        title = str(card.get("title") or "")
        body = str(card.get("body") or "")
        if title in {"后端基建", "WebGL 发布"}:
            return True
        if "多智能体编排" not in body and "整体架构设计" not in body:
            # Short stack-only blurbs from early seed
            if "FastAPI" in body and "\n" not in body:
                return True
            if "WebGL 构建" in body and "\n" not in body:
                return True
    return False


def refresh_tech_intro_section_from_seed(
    store: PlatformStore,
    tenant_id: str,
    seed: dict[str, Any],
) -> bool:
    """Replace empty/placeholder TechIntro cards with seed content."""
    seed_section = next(
        (
            item
            for item in seed.get("sections", [])
            if isinstance(item, dict) and item.get("section_key") == "TechIntro"
        ),
        None,
    )
    if seed_section is None:
        return False
    seed_content = seed_section.get("content")
    if not isinstance(seed_content, dict):
        return False
    seed_cards = seed_content.get("cards")
    if not isinstance(seed_cards, list) or not seed_cards:
        return False

    current = store.get_section(tenant_id, "TechIntro")
    if current is None:
        store.upsert_section(
            tenant_id=tenant_id,
            section_key="TechIntro",
            title=str(seed_section.get("title") or "简历技术介绍"),
            enabled=bool(seed_section.get("enabled", True)),
            sort_order=int(seed_section.get("sort_order", 5)),
            content=dict(seed_content),
        )
        return True

    content = current.content if isinstance(current.content, dict) else {}
    if not _is_placeholder_tech_intro_cards(content.get("cards")):
        return False

    store.upsert_section(
        tenant_id=tenant_id,
        section_key="TechIntro",
        title=str(seed_section.get("title") or current.title or "简历技术介绍"),
        enabled=bool(seed_section.get("enabled", current.enabled)),
        sort_order=int(seed_section.get("sort_order", current.sort_order)),
        content=dict(seed_content),
        section_id=current.id,
    )
    return True


def _is_placeholder_strengths_body(body: Any) -> bool:
    text = str(body or "").strip()
    if not text:
        return True
    return "能把 Unity 交互体验" in text


def refresh_strengths_section_from_seed(
    store: PlatformStore,
    tenant_id: str,
    seed: dict[str, Any],
) -> bool:
    """Replace empty/placeholder Strengths body with seed content."""
    seed_section = next(
        (
            item
            for item in seed.get("sections", [])
            if isinstance(item, dict) and item.get("section_key") == "Strengths"
        ),
        None,
    )
    if seed_section is None:
        return False
    seed_content = seed_section.get("content")
    if not isinstance(seed_content, dict):
        return False
    seed_body = str(seed_content.get("body") or "").strip()
    if not seed_body:
        return False

    current = store.get_section(tenant_id, "Strengths")
    if current is None:
        store.upsert_section(
            tenant_id=tenant_id,
            section_key="Strengths",
            title=str(seed_section.get("title") or "个人优势"),
            enabled=bool(seed_section.get("enabled", True)),
            sort_order=int(seed_section.get("sort_order", 6)),
            content=dict(seed_content),
        )
        return True

    content = current.content if isinstance(current.content, dict) else {}
    if not _is_placeholder_strengths_body(content.get("body")):
        return False

    store.upsert_section(
        tenant_id=tenant_id,
        section_key="Strengths",
        title=str(seed_section.get("title") or current.title or "个人优势"),
        enabled=bool(seed_section.get("enabled", current.enabled)),
        sort_order=int(seed_section.get("sort_order", current.sort_order)),
        content=dict(seed_content),
        section_id=current.id,
    )
    return True


def _is_placeholder_headline(headline: Any) -> bool:
    text = str(headline or "").strip()
    if not text:
        return True
    if text == "罗宇凡 · 简历站点":
        return True
    return "简历站点" in text and "求职意向" not in text


def refresh_headline_from_seed(
    store: PlatformStore,
    tenant_id: str,
    seed: dict[str, Any],
) -> bool:
    """Replace empty/placeholder profile headline (求职意向) with seed content."""
    seed_headline = str(seed.get("headline") or "").strip()
    if not seed_headline:
        return False

    profile = store.get_profile(tenant_id)
    if profile is None:
        store.set_published(tenant_id, True, seed_headline)
        return True
    if not _is_placeholder_headline(profile.headline):
        return False
    if str(profile.headline or "").strip() == seed_headline:
        return False

    store.set_published(tenant_id, profile.published, seed_headline)
    return True


def load_seed_file(name: str = "resume_yufan.json") -> dict[str, Any]:
    path = Path(__file__).resolve().parents[4] / "deploy" / "seeds" / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class MemoryPlatformStore:
    tenants: dict[str, Tenant] = field(default_factory=dict)
    users: dict[str, StoredUser] = field(default_factory=dict)
    profiles: dict[str, ResumeProfile] = field(default_factory=dict)
    sections: dict[str, dict[str, ResumeSection]] = field(default_factory=dict)

    def list_tenants(self) -> list[Tenant]:
        return sorted(self.tenants.values(), key=lambda item: item.slug)

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self.tenants.get(tenant_id)

    def get_tenant_by_slug(self, slug: str) -> Tenant | None:
        for tenant in self.tenants.values():
            if tenant.slug == slug:
                return tenant
        return None

    def create_tenant(
        self,
        *,
        tenant_id: str,
        slug: str,
        display_name: str,
        ai_username: str,
        ai_password: str,
        ai_user_id: str | None = None,
        ai_scopes: list[str] | None = None,
        password_iterations: int = 20_000,
    ) -> Tenant:
        slug = validate_slug(slug)
        if self.get_tenant_by_slug(slug) is not None:
            raise ValueError("slug already exists")
        if tenant_id in self.tenants:
            raise ValueError("tenant already exists")
        if self.get_user(ai_username) is not None:
            raise ValueError("username already exists")
        cleaned_name = display_name.strip() or slug
        tenant = Tenant(
            id=tenant_id,
            slug=slug,
            display_name=cleaned_name,
            assistant_owner=cleaned_name,
            assistant_capabilities=list(DEFAULT_ASSISTANT_CAPABILITIES),
        )
        self.tenants[tenant_id] = tenant
        self.profiles[tenant_id] = ResumeProfile(tenant_id=tenant_id, published=False, headline="")
        self.sections[tenant_id] = {}
        seed_default_sections(self, tenant_id)
        self.upsert_user(
            user_id=ai_user_id or new_id("user"),
            tenant_id=tenant_id,
            username=ai_username,
            password=ai_password,
            scopes=list(ai_scopes or ["tasks:write", "kb:read"]),
            password_iterations=password_iterations,
        )
        return tenant

    def set_tenant_status(self, tenant_id: str, status: str) -> Tenant:
        tenant = self.tenants.get(tenant_id)
        if tenant is None:
            raise LookupError("tenant not found")
        tenant.status = status
        return tenant

    def delete_tenant(self, tenant_id: str) -> None:
        if tenant_id not in self.tenants:
            raise LookupError("tenant not found")
        self.tenants.pop(tenant_id, None)
        self.profiles.pop(tenant_id, None)
        self.sections.pop(tenant_id, None)
        self.users = {
            key: user
            for key, user in self.users.items()
            if user.tenant_id != tenant_id
        }

    def set_tenant_display_name(self, tenant_id: str, display_name: str) -> Tenant:
        return self.update_tenant(tenant_id, display_name=display_name)

    def update_tenant(
        self,
        tenant_id: str,
        *,
        display_name: str | None = None,
        assistant_owner: str | None = None,
        assistant_capabilities: list[str] | None = None,
    ) -> Tenant:
        tenant = self.tenants.get(tenant_id)
        if tenant is None:
            raise LookupError("tenant not found")
        if display_name is not None:
            cleaned = display_name.strip()
            if not cleaned:
                raise ValueError("display_name is required")
            tenant.display_name = cleaned
        if assistant_owner is not None:
            owner = assistant_owner.strip()
            if not owner:
                raise ValueError("assistant_owner is required")
            tenant.assistant_owner = owner
        if assistant_capabilities is not None:
            tenant.assistant_capabilities = normalize_capabilities(assistant_capabilities)
        return tenant

    def upsert_user(
        self,
        *,
        user_id: str,
        tenant_id: str,
        username: str,
        password: str | None = None,
        password_hash: str | None = None,
        scopes: list[str],
        is_platform_admin: bool = False,
        enabled: bool = True,
        password_iterations: int = 20_000,
    ) -> StoredUser:
        existing = self.users.get(username)
        if existing and existing.user_id != user_id:
            raise ValueError("username already exists")
        if tenant_id not in self.tenants:
            # Allow test helpers to attach users to ad-hoc tenant ids.
            self.tenants[tenant_id] = Tenant(
                id=tenant_id,
                slug=tenant_id.removeprefix("tenant_") or tenant_id,
                display_name=tenant_id,
            )
            self.profiles.setdefault(
                tenant_id, ResumeProfile(tenant_id=tenant_id, published=False)
            )
            self.sections.setdefault(tenant_id, {})
        digest = password_hash
        if password is not None:
            digest = hash_password(password, iterations=password_iterations)
        if digest is None and existing is not None:
            digest = existing.password_hash
        if not digest:
            raise ValueError("password required")
        account = StoredUser(
            user_id=user_id,
            tenant_id=tenant_id,
            username=username,
            password_hash=digest,
            scopes=list(scopes),
            is_platform_admin=is_platform_admin,
            enabled=enabled,
        )
        self.users[username] = account
        return account

    def authenticate(self, username: str, password: str) -> StoredUser | None:
        account = self.users.get(username)
        if account is None or not account.enabled:
            return None
        if not verify_password(password, account.password_hash):
            return None
        return account

    def get_user(self, username: str) -> StoredUser | None:
        return self.users.get(username)

    def get_profile(self, tenant_id: str) -> ResumeProfile | None:
        return self.profiles.get(tenant_id)

    def set_published(
        self, tenant_id: str, published: bool, headline: str | None = None
    ) -> ResumeProfile:
        profile = self.profiles.get(tenant_id) or ResumeProfile(
            tenant_id=tenant_id, published=False, headline=""
        )
        profile.published = published
        if headline is not None:
            profile.headline = headline
        self.profiles[tenant_id] = profile
        return profile

    def list_sections(self, tenant_id: str, *, enabled_only: bool = False) -> list[ResumeSection]:
        rows = list(self.sections.get(tenant_id, {}).values())
        if enabled_only:
            rows = [row for row in rows if row.enabled]
        return sorted(rows, key=lambda item: (item.sort_order, item.section_key))

    def get_section(self, tenant_id: str, section_key: str) -> ResumeSection | None:
        return self.sections.get(tenant_id, {}).get(section_key)

    def upsert_section(
        self,
        *,
        tenant_id: str,
        section_key: str,
        title: str,
        enabled: bool,
        sort_order: int,
        content: dict[str, Any],
        section_id: str | None = None,
    ) -> ResumeSection:
        bucket = self.sections.setdefault(tenant_id, {})
        previous = bucket.get(section_key)
        row = ResumeSection(
            id=section_id or (previous.id if previous else new_id("sec")),
            tenant_id=tenant_id,
            section_key=section_key,
            title=title,
            enabled=enabled,
            sort_order=sort_order,
            content=dict(content),
        )
        bucket[section_key] = row
        return row

    def public_resume(self, slug: str) -> dict[str, Any] | None:
        tenant = self.get_tenant_by_slug(slug)
        if tenant is None or tenant.status != "active":
            return None
        profile = self.get_profile(tenant.id)
        if profile is None or not profile.published:
            return None
        sections = self.list_sections(tenant.id, enabled_only=True)
        return {
            "slug": tenant.slug,
            "tenant_id": tenant.id,
            "display_name": tenant.display_name,
            "headline": profile.headline,
            "sections": [
                {
                    "section_key": item.section_key,
                    "title": item.title,
                    "enabled": item.enabled,
                    "sort_order": item.sort_order,
                    "content": public_section_content(item.section_key, item.content),
                }
                for item in sections
            ],
        }


class PostgresPlatformStore:
    def __init__(self, connect) -> None:  # noqa: ANN001
        self._connect = connect

    def list_tenants(self) -> list[Tenant]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, slug, display_name, status,
                       assistant_owner, assistant_capabilities, created_at
                FROM tenants
                ORDER BY created_at DESC NULLS LAST, slug
                """
            ).fetchall()
        return [_tenant_from_row(row) for row in rows]

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, slug, display_name, status,
                       assistant_owner, assistant_capabilities, created_at
                FROM tenants
                WHERE id = %s
                """,
                (tenant_id,),
            ).fetchone()
        return _tenant_from_row(row) if row else None

    def get_tenant_by_slug(self, slug: str) -> Tenant | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, slug, display_name, status,
                       assistant_owner, assistant_capabilities, created_at
                FROM tenants
                WHERE slug = %s
                """,
                (slug,),
            ).fetchone()
        return _tenant_from_row(row) if row else None

    def create_tenant(
        self,
        *,
        tenant_id: str,
        slug: str,
        display_name: str,
        ai_username: str,
        ai_password: str,
        ai_user_id: str | None = None,
        ai_scopes: list[str] | None = None,
        password_iterations: int = 20_000,
    ) -> Tenant:
        slug = validate_slug(slug)
        if self.get_tenant_by_slug(slug) is not None:
            raise ValueError("slug already exists")
        if self.get_user(ai_username) is not None:
            raise ValueError("username already exists")
        cleaned_name = display_name.strip() or slug
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tenants (
                    id, slug, display_name, status,
                    assistant_owner, assistant_capabilities
                )
                VALUES (%s, %s, %s, 'active', %s, %s)
                """,
                (
                    tenant_id,
                    slug,
                    cleaned_name,
                    cleaned_name,
                    list(DEFAULT_ASSISTANT_CAPABILITIES),
                ),
            )
            conn.execute(
                """
                INSERT INTO resume_profiles (tenant_id, published, headline)
                VALUES (%s, false, '')
                """,
                (tenant_id,),
            )
            conn.commit()
        seed_default_sections(self, tenant_id)
        self.upsert_user(
            user_id=ai_user_id or new_id("user"),
            tenant_id=tenant_id,
            username=ai_username,
            password=ai_password,
            scopes=list(ai_scopes or ["tasks:write", "kb:read"]),
            password_iterations=password_iterations,
        )
        tenant = self.get_tenant(tenant_id)
        assert tenant is not None
        return tenant

    def set_tenant_status(self, tenant_id: str, status: str) -> Tenant:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tenants SET status = %s WHERE id = %s",
                (status, tenant_id),
            )
            conn.commit()
        tenant = self.get_tenant(tenant_id)
        if tenant is None:
            raise LookupError("tenant not found")
        return tenant

    def delete_tenant(self, tenant_id: str) -> None:
        if self.get_tenant(tenant_id) is None:
            raise LookupError("tenant not found")
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM gateway_users WHERE tenant_id = %s",
                (tenant_id,),
            )
            conn.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
            conn.commit()

    def set_tenant_display_name(self, tenant_id: str, display_name: str) -> Tenant:
        return self.update_tenant(tenant_id, display_name=display_name)

    def update_tenant(
        self,
        tenant_id: str,
        *,
        display_name: str | None = None,
        assistant_owner: str | None = None,
        assistant_capabilities: list[str] | None = None,
    ) -> Tenant:
        current = self.get_tenant(tenant_id)
        if current is None:
            raise LookupError("tenant not found")
        next_name = current.display_name
        next_owner = current.assistant_owner or current.display_name
        next_caps = list(current.assistant_capabilities or DEFAULT_ASSISTANT_CAPABILITIES)
        if display_name is not None:
            next_name = display_name.strip()
            if not next_name:
                raise ValueError("display_name is required")
        if assistant_owner is not None:
            next_owner = assistant_owner.strip()
            if not next_owner:
                raise ValueError("assistant_owner is required")
        if assistant_capabilities is not None:
            next_caps = normalize_capabilities(assistant_capabilities)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE tenants
                SET display_name = %s,
                    assistant_owner = %s,
                    assistant_capabilities = %s
                WHERE id = %s
                """,
                (next_name, next_owner, next_caps, tenant_id),
            )
            conn.commit()
        tenant = self.get_tenant(tenant_id)
        if tenant is None:
            raise LookupError("tenant not found")
        return tenant

    def upsert_user(
        self,
        *,
        user_id: str,
        tenant_id: str,
        username: str,
        password: str | None = None,
        password_hash: str | None = None,
        scopes: list[str],
        is_platform_admin: bool = False,
        enabled: bool = True,
        password_iterations: int = 20_000,
    ) -> StoredUser:
        existing = self.get_user(username)
        digest = password_hash
        if password is not None:
            digest = hash_password(password, iterations=password_iterations)
        if digest is None and existing is not None:
            digest = existing.password_hash
        if not digest:
            raise ValueError("password required")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gateway_users (
                    user_id, tenant_id, username, password_hash, scopes,
                    is_platform_admin, enabled
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    username = EXCLUDED.username,
                    password_hash = EXCLUDED.password_hash,
                    scopes = EXCLUDED.scopes,
                    is_platform_admin = EXCLUDED.is_platform_admin,
                    enabled = EXCLUDED.enabled
                """,
                (
                    user_id,
                    tenant_id,
                    username,
                    digest,
                    scopes,
                    is_platform_admin,
                    enabled,
                ),
            )
            conn.commit()
        account = self.get_user(username)
        assert account is not None
        return account

    def authenticate(self, username: str, password: str) -> StoredUser | None:
        account = self.get_user(username)
        if account is None or not account.enabled:
            return None
        if not verify_password(password, account.password_hash):
            return None
        return account

    def get_user(self, username: str) -> StoredUser | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, tenant_id, username, password_hash, scopes,
                       is_platform_admin, enabled
                FROM gateway_users
                WHERE username = %s
                """,
                (username,),
            ).fetchone()
        if row is None:
            return None
        return StoredUser(
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            username=row["username"],
            password_hash=row["password_hash"],
            scopes=list(row["scopes"] or []),
            is_platform_admin=bool(row["is_platform_admin"]),
            enabled=bool(row["enabled"]),
        )

    def get_profile(self, tenant_id: str) -> ResumeProfile | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT tenant_id, published, headline
                FROM resume_profiles
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchone()
        return ResumeProfile(**row) if row else None

    def set_published(
        self, tenant_id: str, published: bool, headline: str | None = None
    ) -> ResumeProfile:
        with self._connect() as conn:
            if headline is None:
                conn.execute(
                    """
                    INSERT INTO resume_profiles (tenant_id, published, headline, updated_at)
                    VALUES (%s, %s, '', now())
                    ON CONFLICT (tenant_id) DO UPDATE SET
                        published = EXCLUDED.published,
                        updated_at = now()
                    """,
                    (tenant_id, published),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO resume_profiles (tenant_id, published, headline, updated_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (tenant_id) DO UPDATE SET
                        published = EXCLUDED.published,
                        headline = EXCLUDED.headline,
                        updated_at = now()
                    """,
                    (tenant_id, published, headline),
                )
            conn.commit()
        profile = self.get_profile(tenant_id)
        assert profile is not None
        return profile

    def list_sections(self, tenant_id: str, *, enabled_only: bool = False) -> list[ResumeSection]:
        sql = """
            SELECT id, tenant_id, section_key, title, enabled, sort_order, content
            FROM resume_sections
            WHERE tenant_id = %s
        """
        if enabled_only:
            sql += " AND enabled = true"
        sql += " ORDER BY sort_order, section_key"
        with self._connect() as conn:
            rows = conn.execute(sql, (tenant_id,)).fetchall()
        return [_section_row(row) for row in rows]

    def get_section(self, tenant_id: str, section_key: str) -> ResumeSection | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, tenant_id, section_key, title, enabled, sort_order, content
                FROM resume_sections
                WHERE tenant_id = %s AND section_key = %s
                """,
                (tenant_id, section_key),
            ).fetchone()
        return _section_row(row) if row else None

    def upsert_section(
        self,
        *,
        tenant_id: str,
        section_key: str,
        title: str,
        enabled: bool,
        sort_order: int,
        content: dict[str, Any],
        section_id: str | None = None,
    ) -> ResumeSection:
        previous = self.get_section(tenant_id, section_key)
        row_id = section_id or (previous.id if previous else new_id("sec"))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO resume_sections (
                    id, tenant_id, section_key, title, enabled, sort_order, content, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, now())
                ON CONFLICT (tenant_id, section_key) DO UPDATE SET
                    title = EXCLUDED.title,
                    enabled = EXCLUDED.enabled,
                    sort_order = EXCLUDED.sort_order,
                    content = EXCLUDED.content,
                    updated_at = now()
                """,
                (
                    row_id,
                    tenant_id,
                    section_key,
                    title,
                    enabled,
                    sort_order,
                    json.dumps(content, ensure_ascii=False),
                ),
            )
            conn.commit()
        section = self.get_section(tenant_id, section_key)
        assert section is not None
        return section

    def public_resume(self, slug: str) -> dict[str, Any] | None:
        tenant = self.get_tenant_by_slug(slug)
        if tenant is None or tenant.status != "active":
            return None
        profile = self.get_profile(tenant.id)
        if profile is None or not profile.published:
            return None
        sections = self.list_sections(tenant.id, enabled_only=True)
        return {
            "slug": tenant.slug,
            "tenant_id": tenant.id,
            "display_name": tenant.display_name,
            "headline": profile.headline,
            "sections": [
                {
                    "section_key": item.section_key,
                    "title": item.title,
                    "enabled": item.enabled,
                    "sort_order": item.sort_order,
                    "content": public_section_content(item.section_key, item.content),
                }
                for item in sections
            ],
        }


def _section_row(row: dict[str, Any]) -> ResumeSection:
    content = row["content"]
    if isinstance(content, str):
        content = json.loads(content)
    return ResumeSection(
        id=row["id"],
        tenant_id=row["tenant_id"],
        section_key=row["section_key"],
        title=row["title"],
        enabled=bool(row["enabled"]),
        sort_order=int(row["sort_order"]),
        content=dict(content or {}),
    )


def _ensure_shell_tenant(store: PlatformStore, *, tenant_id: str, slug: str, display_name: str) -> None:
    if store.get_tenant(tenant_id) is not None or store.get_tenant_by_slug(slug) is not None:
        return
    if isinstance(store, MemoryPlatformStore):
        store.tenants[tenant_id] = Tenant(
            id=tenant_id,
            slug=slug,
            display_name=display_name,
            status="active",
            assistant_owner=display_name,
            assistant_capabilities=list(DEFAULT_ASSISTANT_CAPABILITIES),
        )
        store.profiles[tenant_id] = ResumeProfile(
            tenant_id=tenant_id, published=False, headline=""
        )
        store.sections[tenant_id] = {}
        return
    if isinstance(store, PostgresPlatformStore):
        with store._connect() as conn:  # noqa: SLF001
            conn.execute(
                """
                INSERT INTO tenants (
                    id, slug, display_name, status,
                    assistant_owner, assistant_capabilities
                )
                VALUES (%s, %s, %s, 'active', %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    tenant_id,
                    slug,
                    display_name,
                    display_name,
                    list(DEFAULT_ASSISTANT_CAPABILITIES),
                ),
            )
            conn.execute(
                """
                INSERT INTO resume_profiles (tenant_id, published, headline)
                VALUES (%s, false, '')
                ON CONFLICT (tenant_id) DO NOTHING
                """,
                (tenant_id,),
            )
            conn.commit()


def ensure_platform_bootstrap(
    store: PlatformStore,
    *,
    settings: Any,
) -> None:
    """Ensure platform admin, bootstrap AI tenant, and resume seed rows exist."""
    iterations = int(getattr(settings, "password_iterations", 20_000))
    admin_username = getattr(settings, "admin_username", None)
    admin_password = getattr(settings, "admin_password", None)
    if admin_username and admin_password:
        platform_tenant_id = "tenant_platform"
        _ensure_shell_tenant(
            store,
            tenant_id=platform_tenant_id,
            slug="platform",
            display_name="Platform",
        )
        scopes = list(getattr(settings, "admin_scope_list", []))
        if "platform:admin" not in scopes:
            scopes.append("platform:admin")
        for needed in ("tasks:write", "kb:read"):
            if needed not in scopes:
                scopes.append(needed)
        store.upsert_user(
            user_id=getattr(settings, "admin_user_id", "user_admin"),
            tenant_id=platform_tenant_id,
            username=admin_username,
            password=admin_password,
            scopes=scopes,
            is_platform_admin=True,
            password_iterations=iterations,
        )

    bootstrap_username = getattr(settings, "bootstrap_username", None)
    bootstrap_password = getattr(settings, "bootstrap_password", None)
    if not bootstrap_username or not bootstrap_password:
        return

    tenant_id = str(getattr(settings, "bootstrap_tenant_id", "tenant_dev") or "tenant_dev")
    slug = tenant_id.removeprefix("tenant_") or "dev"
    # First-boot label only; after creation rename via PATCH /admin/tenants/{slug}.
    display_name = str(getattr(settings, "bootstrap_display_name", "") or "").strip() or (
        "罗宇凡" if slug == "yufan" else slug
    )
    seed = load_seed_file() if slug == "yufan" else {}

    existing = store.get_tenant(tenant_id) or store.get_tenant_by_slug(slug)
    if existing is None:
        store.create_tenant(
            tenant_id=tenant_id,
            slug=slug,
            display_name=display_name,
            ai_username=bootstrap_username,
            ai_password=bootstrap_password,
            ai_user_id=getattr(settings, "bootstrap_user_id", "user_dev"),
            ai_scopes=list(getattr(settings, "bootstrap_scope_list", ["tasks:write", "kb:read"])),
            password_iterations=iterations,
        )
        if slug == "yufan":
            store.update_tenant(
                tenant_id,
                assistant_owner="张博洋",
                assistant_capabilities=list(DEFAULT_ASSISTANT_CAPABILITIES),
            )
        if seed:
            seed_default_sections(store, tenant_id, seed)
        else:
            store.set_published(tenant_id, True, "")
    else:
        store.upsert_user(
            user_id=getattr(settings, "bootstrap_user_id", "user_dev"),
            tenant_id=existing.id,
            username=bootstrap_username,
            password=bootstrap_password,
            scopes=list(getattr(settings, "bootstrap_scope_list", ["tasks:write", "kb:read"])),
            password_iterations=iterations,
        )
        if not store.list_sections(existing.id):
            seed_default_sections(store, existing.id, seed or None)
        elif seed:
            refresh_skills_section_from_seed(store, existing.id, seed)
            refresh_work_experience_section_from_seed(store, existing.id, seed)
            refresh_projects_section_from_seed(store, existing.id, seed)
            refresh_education_section_from_seed(store, existing.id, seed)
            refresh_tech_intro_section_from_seed(store, existing.id, seed)
            refresh_strengths_section_from_seed(store, existing.id, seed)
            refresh_headline_from_seed(store, existing.id, seed)
        profile = store.get_profile(existing.id)
        if profile is not None and not profile.published:
            headline = profile.headline or (str(seed.get("headline") or "") if seed else "")
            store.set_published(existing.id, True, headline)