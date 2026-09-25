"""Session control skills: end the session, start a fresh conversation."""

from __future__ import annotations

from .context import SkillContext
from .registry import skill


@skill()
def end_session(ctx: SkillContext = None) -> str:
    """Stop listening and shut the assistant down (user said goodbye, exit, stop listening)."""
    ctx.stop_event.set()
    return "Goodbye."


@skill()
def new_conversation(ctx: SkillContext = None) -> str:
    """Forget the current conversation context and start fresh."""
    ctx.reset_conversation()
    return "Starting fresh."
