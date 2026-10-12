"""Scan skills/*/SKILL.md and run the script named in each file."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

SKILLS_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class SkillFolder:
    name: str
    description: str
    body: str
    directory: Path
    entrypoint: Path


def load_skills() -> dict[str, SkillFolder]:
    """Find each skill folder. This only reads the markdown; it does not call the script."""
    skills: dict[str, SkillFolder] = {}
    for skill_md in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        meta, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        skill_dir = skill_md.parent
        name = (meta.get("name") or skill_dir.name).strip()
        description = (meta.get("description") or "").strip()
        entrypoint = _discover_entrypoint(skill_dir, meta.get("entrypoint"))
        if not name or not description or entrypoint is None:
            continue
        skills[name] = SkillFolder(
            name=name,
            description=description,
            body=body.strip(),
            directory=skill_dir,
            entrypoint=entrypoint,
        )
    return skills


def format_catalog(allowed: Iterable[str] | None = None) -> str:
    skills = load_skills()
    if allowed is not None:
        allow = {str(name).strip() for name in allowed if str(name).strip()}
        skills = {name: spec for name, spec in skills.items() if name in allow}
    if not skills:
        return "（当前没有可用 Skill）"
    return "\n".join(f"- {spec.name}: {spec.description}" for spec in skills.values())


def get_skill(name: str) -> SkillFolder | None:
    skills = load_skills()
    raw = (name or "").strip()
    if raw in skills:
        return skills[raw]
    lowered = raw.casefold()
    for key, spec in skills.items():
        if key.casefold() == lowered:
            return spec
    return None


def load_skill_markdown(name: str) -> str:
    spec = get_skill(name)
    if spec is None:
        available = "、".join(load_skills()) or "无"
        raise ValueError(f"找不到 Skill「{name}」。当前可用：{available}")
    return f"# Skill: {spec.name}\n\n{spec.description}\n\n{spec.body}"


def skill_module(name: str) -> ModuleType:
    spec = get_skill(name)
    if spec is None:
        available = "、".join(load_skills()) or "无"
        raise ValueError(f"找不到 Skill「{name}」。当前可用：{available}")
    return _load_module(spec)


def run_skill(name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """Load the skill script and call its run function."""
    spec = get_skill(name)
    if spec is None:
        available = "、".join(load_skills()) or "无"
        raise ValueError(f"找不到 Skill「{name}」。当前可用：{available}")
    module = skill_module(name)
    run = getattr(module, "run", None)
    if not callable(run):
        raise TypeError(f"Skill「{name}」的脚本没有 run 函数。")
    result = run(arguments or {}, **kwargs)
    if not isinstance(result, dict):
        raise TypeError(f"Skill「{name}」没有返回对象。")
    return result


def _load_module(spec: SkillFolder) -> ModuleType:
    module_name = f"fs_agent_runtime_skill_{spec.entrypoint.stem}"
    module_spec = importlib.util.spec_from_file_location(module_name, spec.entrypoint)
    if module_spec is None or module_spec.loader is None:
        raise ValueError(f"Skill「{spec.name}」脚本加载失败。")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def _discover_entrypoint(skill_dir: Path, relative: str | None) -> Path | None:
    skill_dir = skill_dir.resolve()
    if relative:
        candidate = (skill_dir / relative).resolve()
        if candidate.is_file() and skill_dir in candidate.parents:
            return candidate
        return None
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        return None
    scripts = sorted(path for path in scripts_dir.glob("*.py") if path.name != "__init__.py")
    return scripts[0] if scripts else None


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return {}, stripped
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}, stripped
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, parts[2].lstrip("\n")
