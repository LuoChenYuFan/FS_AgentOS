"""One folder per skill. Each folder has SKILL.md and a script."""

from fs_agent_runtime.skills.loader import get_skill, load_skills, run_skill

__all__ = ["get_skill", "load_skills", "run_skill"]
