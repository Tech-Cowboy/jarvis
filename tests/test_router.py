from __future__ import annotations

from daxton.brain.base import LLM, LLMError, LLMResponse, Message, ToolCall
from daxton.brain.router import Router
from daxton.skills.context import SkillContext
from daxton.skills.registry import SkillRegistry


class ScriptedLLM(LLM):
    """Returns canned responses in order; records what it was asked."""

    name = "scripted"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, system, messages, tools):
        self.calls.append((system, list(messages), tools))
        if not self.responses:
            return LLMResponse(text="(no more script)")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_router(settings, responses, **kw):
    reg = SkillRegistry()

    def add(a: int, b: int) -> str:
        """Add numbers."""
        return str(a + b)

    def reset_me(ctx: SkillContext = None) -> str:
        """Reset."""
        ctx.reset_conversation()
        return "reset"

    reg.add(add)
    reg.add(reset_me)
    ctx = SkillContext(settings=settings)
    llm = ScriptedLLM(responses)
    return Router(llm, reg, lambda: "SYS", ctx, **kw), llm


def test_plain_reply(settings):
    router, llm = make_router(settings, [LLMResponse(text="Hello there.")])
    assert router.ask("hi") == "Hello there."
    assert [m.role for m in router.history] == ["user", "assistant"]
    system, messages, tools = llm.calls[0]
    assert system == "SYS" and messages[0].content == "hi" and {t.name for t in tools} == {"add", "reset_me"}


def test_tool_loop(settings):
    seen = []
    router, llm = make_router(
        settings,
        [
            LLMResponse(text="", tool_calls=[ToolCall("c1", "add", {"a": 2, "b": 3})]),
            LLMResponse(text="Two plus three is five."),
        ],
        on_tool=lambda n, a, r: seen.append((n, a, r)),
    )
    assert router.ask("what is 2+3") == "Two plus three is five."
    assert seen == [("add", {"a": 2, "b": 3}, "5")]
    roles = [m.role for m in router.history]
    assert roles == ["user", "assistant", "tool", "assistant"]
    tool_msg = router.history[2]
    assert tool_msg.tool_call_id == "c1" and tool_msg.tool_name == "add" and tool_msg.content == "5"
    # the second model call saw the tool result
    assert llm.calls[1][1][-1].role == "tool"


def test_unknown_tool_is_reported_to_model(settings):
    router, llm = make_router(settings, [
        LLMResponse(tool_calls=[ToolCall("c1", "nonexistent", {})]),
        LLMResponse(text="Sorry."),
    ])
    router.ask("do it")
    assert "unknown tool" in router.history[2].content


def test_max_tool_rounds(settings):
    looping = [LLMResponse(tool_calls=[ToolCall(f"c{i}", "add", {"a": 1, "b": 1})]) for i in range(10)]
    router, _ = make_router(settings, looping, max_tool_rounds=3)
    reply = router.ask("loop forever")
    assert "ran out of steps" in reply


def test_llm_error_drops_failed_turn(settings):
    router, _ = make_router(settings, [LLMError("boom"), LLMResponse(text="ok now")])
    reply = router.ask("first")
    assert reply.startswith("I couldn't reach my language model")
    assert router.history == []
    assert router.ask("second") == "ok now"


def test_history_window_keeps_whole_turns(settings):
    responses = [LLMResponse(text=f"r{i}") for i in range(6)]
    router, llm = make_router(settings, responses, history_turns=2)
    for i in range(6):
        router.ask(f"q{i}")
    window = llm.calls[-1][1]
    assert [m.content for m in window if m.role == "user"] == ["q4", "q5"]
    assert window[0].role == "user"


def test_reset_requested_by_skill_applies_after_turn(settings):
    router, _ = make_router(settings, [
        LLMResponse(tool_calls=[ToolCall("c1", "reset_me", {})]),
        LLMResponse(text="Fresh start."),
    ])
    assert router.ask("start over") == "Fresh start."
    assert router.history == []
