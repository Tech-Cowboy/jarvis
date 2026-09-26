"""Session control skills: end the session, start a fresh conversation."""

from __future__ import annotations

from .context import SkillContext
from .registry import skill


@skill()
def end_session(ctx: SkillContext = None) -> str:
    """End the conversation (user said goodbye, that's all, stop listening). Outside a conversation this shuts the assistant down."""
    try:
        if ctx.end_conversation():
            return "Goodbye."
    except Exception:
        pass
    ctx.stop_event.set()
    return "Goodbye."


@skill()
def new_conversation(ctx: SkillContext = None) -> str:
    """Forget the current conversation context and start fresh."""
    ctx.reset_conversation()
    return "Starting fresh."
