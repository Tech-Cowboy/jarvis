from __future__ import annotations

from typing import Annotated

from jarvis.skills.context import SkillContext
from jarvis.skills.registry import SkillRegistry


def make_registry():
    reg = SkillRegistry()

    def greet(name: Annotated[str, "Who to greet."], excited: bool = False, times: int = 1) -> str:
        """Say hello."""
        return ("HELLO " if excited else "hello ") + name + "!" * times

    def needs_ctx(ctx: SkillContext = None) -> str:
        """Uses the context."""
        return ctx.settings.assistant_name

    def boom() -> str:
        """Always fails."""
        raise RuntimeError("kaboom")

    reg.add(greet)
    reg.add(needs_ctx)
    reg.add(boom)
    return reg


def test_schema_from_annotations():
    reg = make_registry()
    spec = reg.get("greet").spec()
    assert spec.description == "Say hello."
    props = spec.parameters["properties"]
    assert props["name"] == {"type": "string", "description": "Who to greet."}
    assert props["excited"] == {"type": "boolean"}
    assert props["times"] == {"type": "integer"}
    assert spec.parameters["required"] == ["name"]
    # ctx is injected, never exposed to the model
    assert "ctx" not in reg.get("needs_ctx").spec().parameters["properties"]
    assert reg.get("needs_ctx").wants_ctx


def test_run_with_args_and_ctx(settings):
    reg = make_registry()
    ctx = SkillContext(settings=settings)
    assert reg.run("greet", {"name": "Zach", "excited": True, "times": 2}, ctx) == "HELLO Zach!!"
    assert reg.run("needs_ctx", {}, ctx) == "Jarvis"


def test_run_errors_are_strings(settings):
    reg = make_registry()
    ctx = SkillContext(settings=settings)
    assert reg.run("nope", {}, ctx).startswith("Error: unknown tool")
    assert "missing required" in reg.run("greet", {}, ctx)
    assert reg.run("boom", {}, ctx) == "Error: boom failed: kaboom"
    # unknown arguments are dropped rather than crashing
    assert reg.run("greet", {"name": "A", "bogus": 1}, ctx) == "hello A!"


def test_default_skills_load():
    from jarvis.skills import load_default_skills

    reg = load_default_skills()
    names = set(reg.names())
    for expected in ("open_app", "quit_app", "web_search", "open_website", "current_datetime", "set_timer",
                     "remember", "recall_notes", "find_files", "end_session", "wikipedia_summary"):
        assert expected in names
    for spec in reg.tool_specs():
        assert spec.parameters["type"] == "object"
        assert spec.description
