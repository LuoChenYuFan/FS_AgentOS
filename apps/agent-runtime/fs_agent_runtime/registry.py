"""Agent and skill catalogs. A bad catalog fails at startup, before any request."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_CATALOG = Path(__file__).parent / "catalog"


class CatalogError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


class AgentSpec(BaseModel):
    name: str
    when: str
    not_when: str = ""
    allowed_skills: list[str] = Field(default_factory=list)
    produces: dict[str, Any] = Field(default_factory=dict)
    side_effect: str = "none"
    needs_approval: bool = False
    loop: str = ""
    model_tier: str = "cheap"

    def field_types(self) -> dict[str, str]:
        return _field_types(self.produces)


class SkillDecl(BaseModel):
    name: str
    input: dict[str, Any] = Field(default_factory=dict)
    produces: dict[str, Any] = Field(default_factory=dict)
    side_effect: str = "none"
    timeout: str = "30s"


class SkillBinding(BaseModel):
    agent: str
    skill: str
    timeout: str = "30s"
    max_results: int = 10


class Catalog(BaseModel):
    agents: list[AgentSpec]
    skills: list[SkillDecl]
    bindings: list[SkillBinding] = Field(default_factory=list)

    def agent(self, name: str) -> AgentSpec | None:
        for item in self.agents:
            if item.name == name:
                return item
        return None

    def skill_names(self) -> set[str]:
        return {item.name for item in self.skills}


def load_catalog(directory: Path | None = None) -> Catalog:
    root = directory or _CATALOG
    agents = _read_yaml(root / "agents.yaml")
    raw_skills = _read_yaml(root / "skills.yaml")
    if isinstance(raw_skills, dict):
        skills = raw_skills.get("skills", [])
        bindings = raw_skills.get("bindings", [])
    else:
        skills = raw_skills
        bindings = []
    catalog = Catalog.model_validate({"agents": agents, "skills": skills, "bindings": bindings})
    errors = validate_catalog(catalog)
    if errors:
        raise CatalogError(errors)
    return catalog


def validate_catalog(catalog: Catalog) -> list[str]:
    errors: list[str] = []
    agent_names = [item.name for item in catalog.agents]
    if len(agent_names) != len(set(agent_names)):
        errors.append("agent names must be unique")
    skill_names = [item.name for item in catalog.skills]
    if len(skill_names) != len(set(skill_names)):
        errors.append("skill names must be unique")
    known_skills = set(skill_names)
    seen_when: dict[str, str] = {}
    for agent in catalog.agents:
        if not agent.when.strip():
            errors.append(f"{agent.name} is missing when")
        if agent.when.strip() and agent.when.strip() == agent.not_when.strip():
            errors.append(f"{agent.name} has the same when and not_when")
        previous = seen_when.get(agent.when.strip())
        if previous:
            errors.append(f"{agent.name} and {previous} claim the same when")
        elif agent.when.strip():
            seen_when[agent.when.strip()] = agent.name
        if not agent.field_types():
            errors.append(f"{agent.name} does not declare produces")
        unknown = [name for name in agent.allowed_skills if name not in known_skills]
        if unknown:
            errors.append(f"{agent.name} allows unknown skills: {unknown}")
        if agent.side_effect not in {"none", "irreversible"}:
            errors.append(f"{agent.name} has an unknown side_effect")
        if agent.side_effect == "irreversible" and not agent.needs_approval:
            errors.append(f"{agent.name} is irreversible and needs approval")
    known_agents = set(agent_names)
    for binding in catalog.bindings:
        if binding.agent not in known_agents:
            errors.append(f"binding names unknown agent {binding.agent}")
        if binding.skill not in known_skills:
            errors.append(f"binding names unknown skill {binding.skill}")
    return errors


def _field_types(produces: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, value in produces.items():
        if isinstance(value, dict):
            for child, kind in value.items():
                found[f"{key}.{child}"] = str(kind)
        else:
            found[str(key)] = str(value)
    return found


def _read_yaml(path: Path) -> object:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return []
    return loaded
