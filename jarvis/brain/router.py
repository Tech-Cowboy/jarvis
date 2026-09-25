"""The agent loop: user text in, spoken reply out, tools in between."""

from __future__ import annotations

import logging
from typing import Callable

from ..skills.registry import SkillRegistry
from ..skills.context import SkillContext
from .base import LLM, LLMError, Message

log = logging.getLogger(__name__)


class Router:
    def __init__(
        self,
        llm: LLM,
        registry: SkillRegistry,
        system_prompt: Callable[[], str],
        ctx: SkillContext,
        history_turns: int = 8,
        max_tool_rounds: int = 6,
        on_tool: Callable[[str, dict, str], None] | None = None,
    ):
        self.llm = llm
        self.registry = registry
        self.system_prompt = system_prompt
        self.ctx = ctx
        self.history_turns = history_turns
        self.max_tool_rounds = max_tool_rounds
        self.on_tool = on_tool  # called with (name, arguments, result) after each tool run
        self.history: list[Message] = []
        self._reset_pending = False
        ctx.reset_conversation = self.request_reset

    # ------------------------------------------------------------------ public
    def ask(self, user_text: str) -> str:
        """Run one user turn through the model and any tools it calls. Returns the spoken reply."""
        user_text = user_text.strip()
        if not user_text:
            return ""
        try:
            return self._ask(user_text)
        finally:
            if self._reset_pending:
                self._reset_pending = False
                self.history.clear()

    def reset(self) -> None:
        self.history.clear()

    def request_reset(self) -> None:
        """Clear the history once the current turn has finished (safe to call from a skill)."""
        self._reset_pending = True

    # ----------------------------------------------------------------- private
    def _ask(self, user_text: str) -> str:
        self.history.append(Message("user", user_text))
        tools = self.registry.tool_specs()

        for _round in range(self.max_tool_rounds):
            try:
                resp = self.llm.complete(self.system_prompt(), self._window(), tools)
            except LLMError as e:
                log.error("LLM error: %s", e)
                self._drop_current_turn()
                return f"I couldn't reach my language model. {_short(str(e))}"

            if not resp.tool_calls:
                reply = resp.text or "Done."
                self.history.append(Message("assistant", reply))
                return reply

            self.history.append(Message("assistant", resp.text, tool_calls=resp.tool_calls))
            for call in resp.tool_calls:
                log.info("tool %s(%s)", call.name, call.arguments)
                result = self.registry.run(call.name, call.arguments, self.ctx)
                log.debug("tool %s -> %s", call.name, result[:200])
                if self.on_tool:
                    try:
                        self.on_tool(call.name, call.arguments, result)
                    except Exception:  # a display hook must never break the loop
                        pass
                self.history.append(Message("tool", result, tool_call_id=call.id, tool_name=call.name))

        reply = "I ran out of steps trying to do that. Could you try a simpler request?"
        self.history.append(Message("assistant", reply))
        return reply

    def _drop_current_turn(self) -> None:
        """Remove everything since the last user message (a failed turn) so history stays valid."""
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i].role == "user":
                del self.history[i:]
                return

    def _window(self) -> list[Message]:
        """Keep the last N user turns, always starting at a user message so tool chains stay intact."""
        user_indexes = [i for i, m in enumerate(self.history) if m.role == "user"]
        if len(user_indexes) <= self.history_turns:
            return list(self.history)
        start = user_indexes[-self.history_turns]
        self.history = self.history[start:]
        return list(self.history)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
