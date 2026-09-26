"""Skills: the things Daxton can do. Importing a module registers its @skill functions."""

from __future__ import annotations

import importlib

from .context import SkillContext
from .registry import SkillRegistry, default_registry, skill

DEFAULT_SKILL_MODULES = [
    "daxton.skills.apps",
    "daxton.skills.web",
    "daxton.skills.system",
    "daxton.skills.files",
    "daxton.skills.notes",
    "daxton.skills.timers",
    "daxton.skills.control",
    "daxton.skills.work",
    "daxton.skills.business",
]


def load_default_skills(extra_modules: list[str] | None = None, settings=None) -> SkillRegistry:
    """Import every built-in skill module (plus any extras) into the default registry.

    With settings, the business skills are dropped when no business system is configured, so the brain and the
    conversation agent never see tools that cannot work.
    """
    for mod in DEFAULT_SKILL_MODULES + list(extra_modules or []):
        importlib.import_module(mod)
    if settings is not None:
        from . import business

        business.ensure_registered(default_registry, settings.business_configured())
    return default_registry


__all__ = ["SkillContext", "SkillRegistry", "default_registry", "skill", "load_default_skills"]
