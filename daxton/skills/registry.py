"""Skill registry: plain Python functions become tools the brain can call.

    from typing import Annotated
    from daxton.skills.registry import skill

    @skill()
    def open_app(name: Annotated[str, "Application name, e.g. 'Safari'"]) -> str:
        \"\"\"Open (launch) an application on this computer.\"\"\"
        ...

The docstring becomes the tool description, the annotations become the JSON
schema, and a parameter named `ctx` receives the SkillContext (settings,
speaker, session control). Every skill returns a short string that is either
spoken verbatim (keyword mode) or summarized by the LLM.
"""

from __future__ import annotations

import inspect
import logging
import typing
from dataclasses import dataclass
from typing import Any, Callable, get_args, get_origin, get_type_hints

from ..brain.base import ToolSpec
from .context import SkillContext

log = logging.getLogger(__name__)

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


@dataclass
class Skill:
    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., str]
    wants_ctx: bool

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)


def _schema_for(func: Callable[..., Any]) -> tuple[dict[str, Any], bool]:
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func, include_extras=True)
    except Exception:  # pragma: no cover - exotic annotations
        hints = {}
    props: dict[str, Any] = {}
    required: list[str] = []
    wants_ctx = False
    for pname, param in sig.parameters.items():
        if pname == "ctx":
            wants_ctx = True
            continue
        hint = hints.get(pname, str)
        desc = ""
        if get_origin(hint) is typing.Annotated:
            base, *extras = get_args(hint)
            hint = base
            desc = " ".join(str(x) for x in extras if isinstance(x, str))
        origin = get_origin(hint)
        if origin is typing.Union or str(origin) == "types.UnionType":
            args = [a for a in get_args(hint) if a is not type(None)]
            hint = args[0] if args else str
        jtype = _JSON_TYPES.get(hint, "string")
        prop: dict[str, Any] = {"type": jtype}
        if desc:
            prop["description"] = desc
        if jtype == "array":
            prop["items"] = {"type": "string"}
        props[pname] = prop
        if param.default is inspect.Parameter.empty:
            required.append(pname)
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema, wants_ctx


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def add(self, func: Callable[..., str], name: str | None = None, description: str | None = None) -> Skill:
        schema, wants_ctx = _schema_for(func)
        sk = Skill(
            name=name or func.__name__,
            description=(description or inspect.getdoc(func) or func.__name__).strip(),
            parameters=schema,
            func=func,
            wants_ctx=wants_ctx,
        )
        self._skills[sk.name] = sk
        return sk

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def __len__(self) -> int:
        return len(self._skills)

    def tool_specs(self) -> list[ToolSpec]:
        return [s.spec() for s in self._skills.values()]

    def run(self, name: str, arguments: dict[str, Any] | None, ctx: SkillContext) -> str:
        sk = self._skills.get(name)
        if sk is None:
            return f"Error: unknown tool '{name}'."
        kwargs = dict(arguments or {})
        allowed = set(sk.parameters.get("properties", {}))
        unknown = set(kwargs) - allowed
        for k in unknown:
            kwargs.pop(k)
        missing = [p for p in sk.parameters.get("required", []) if p not in kwargs]
        if missing:
            return f"Error: tool '{name}' is missing required argument(s): {', '.join(missing)}."
        if sk.wants_ctx:
            kwargs["ctx"] = ctx
        try:
            result = sk.func(**kwargs)
        except Exception as e:  # a skill must never take the assistant down
            log.exception("skill %s failed", name)
            return f"Error: {name} failed: {e}"
        if result is None:
            return "Done."
        return str(result)


# A process-wide default registry that the @skill decorator populates.
default_registry = SkillRegistry()


def skill(name: str | None = None, description: str | None = None, registry: SkillRegistry | None = None):
    def decorator(func: Callable[..., str]) -> Callable[..., str]:
        (registry or default_registry).add(func, name=name, description=description)
        return func

    return decorator
