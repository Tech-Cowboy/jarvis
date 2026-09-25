from __future__ import annotations

import pytest

from daxton.brain.base import LLM, LLMError, LLMResponse, Message, ToolCall, ToolSpec
from daxton.brain.keyword_brain import KeywordBrain
from daxton.brain.rating import ComplexityRater
from daxton.brain.router import Router
from daxton.brain.tiered import TieredLLM
from daxton.skills import load_default_skills
from daxton.skills.context import SkillContext

TOOLS = [ToolSpec(n, "", {"type": "object", "properties": {}}) for n in load_default_skills().names()]


class FakeLLM(LLM):
    def __init__(self, name, fail=False, reply=None):
        self.name = name
        self.model = name + "-model"
        self.fail = fail
        self.reply = reply or (lambda msgs: LLMResponse(text=f"{name} says hi"))
        self.calls = 0

    def complete(self, system, messages, tools):
        self.calls += 1
        if self.fail:
            raise LLMError(f"{self.name} is down")
        return self.reply(messages)


def make(free=None, fast=None, smart=None, escalate=True, pinned=None, free_is_llm=None):
    tiers = {"free": free, "fast": fast, "smart": smart}
    is_llm = free_is_llm if free_is_llm is not None else not isinstance(free, KeywordBrain)
    return TieredLLM(tiers, ComplexityRater(free_is_llm=is_llm), escalate=escalate, pinned=pinned)


def user(text):
    return [Message("user", text)]


def test_routes_by_complexity():
    free, fast, smart = FakeLLM("free"), FakeLLM("fast"), FakeLLM("smart")
    t = make(free, fast, smart)
    assert t.complete("s", user("open safari"), TOOLS).text == "free says hi"
    assert t.complete("s", user("tell me a joke about horses"), TOOLS).text == "fast says hi"
    assert t.complete("s", user("compare the pros and cons of buying versus leasing a horse trailer"), TOOLS).text == "smart says hi"
    assert t.stats["turns:free"] == 1 and t.stats["turns:fast"] == 1 and t.stats["turns:smart"] == 1
    assert t.last_route.tier == "smart" and "smart" in t.last_route.llm and t.last_route.rating.score >= 0.6
    assert t.summary() == "Routed turns: free 1, fast 1, smart 1."


def test_escalates_when_tier_fails():
    free, fast, smart = FakeLLM("free", fail=True), FakeLLM("fast", fail=True), FakeLLM("smart")
    t = make(free, fast, smart)
    resp = t.complete("s", user("open safari"), TOOLS)
    assert resp.text == "smart says hi"
    assert t.last_route.escalated and t.stats["escalations"] == 1
    assert free.calls == 1 and fast.calls == 1 and smart.calls == 1


def test_no_escalation_when_disabled():
    t = make(FakeLLM("free", fail=True), FakeLLM("fast"), None, escalate=False)
    with pytest.raises(LLMError):
        t.complete("s", user("open safari"), TOOLS)


def test_all_tiers_down_raises_with_every_error():
    t = make(FakeLLM("free", fail=True), FakeLLM("fast", fail=True), FakeLLM("smart", fail=True))
    with pytest.raises(LLMError) as e:
        t.complete("s", user("open safari"), TOOLS)
    assert "free" in str(e.value) and "smart is down" in str(e.value)


def test_keyword_miss_escalates_to_a_model():
    fast = FakeLLM("fast")
    t = make(KeywordBrain(), fast, None)
    # a plain command stays with the keyword brain (a tool call, not text)
    resp = t.complete("s", user("open safari"), TOOLS)
    assert resp.tool_calls and resp.tool_calls[0].name == "open_app" and fast.calls == 0
    # something the rules cannot parse is rated for the fast tier directly
    resp = t.complete("s", user("bring up my calculator please, would you"), TOOLS)
    assert resp.text == "fast says hi" and fast.calls == 1
    # and if the rater still sends it to keyword but the rules miss, the miss escalates
    t2 = make(KeywordBrain(), fast, None, free_is_llm=True)
    resp = t2.complete("s", user("bring up my calculator please, would you"), TOOLS)
    assert resp.text == "fast says hi" and t2.last_route.escalated


def test_keyword_miss_without_higher_tier_is_returned():
    t = make(KeywordBrain(), None, None)
    resp = t.complete("s", user("please write me a sonnet"), TOOLS)
    assert resp.miss and "didn't catch" in resp.text


def test_tool_loop_keeps_the_same_tier():
    free = FakeLLM("free")
    fast = FakeLLM("fast", reply=lambda msgs: (
        LLMResponse(tool_calls=[ToolCall("c1", "current_datetime", {})]) if msgs[-1].role == "user"
        else LLMResponse(text="fast finished")))
    smart = FakeLLM("smart")
    t = make(free, fast, smart)
    msgs = user("tell me a joke about the time")
    r1 = t.complete("s", msgs, TOOLS)
    assert r1.tool_calls and fast.calls == 1
    msgs += [Message("assistant", "", r1.tool_calls), Message("tool", "It is noon.", tool_call_id="c1", tool_name="current_datetime")]
    r2 = t.complete("s", msgs, TOOLS)
    assert r2.text == "fast finished" and fast.calls == 2 and free.calls == 0 and smart.calls == 0
    assert t.stats["turns:fast"] == 1 and t.stats["calls:fast"] == 2


def test_follow_up_sticks_with_previous_tier():
    free, fast, smart = FakeLLM("free"), FakeLLM("fast"), FakeLLM("smart")
    t = make(free, fast, smart)
    t.complete("s", user("compare the pros and cons of buying versus leasing a horse trailer"), TOOLS)
    assert t.complete("s", user("and the second one?"), TOOLS).text == "smart says hi"
    assert "sticks with previous tier" in " ".join(t.last_route.rating.reasons)
    # a fresh simple command drops back down
    assert t.complete("s", user("open safari"), TOOLS).text == "free says hi"


def test_pinned_tier_and_missing_tiers():
    free, smart = FakeLLM("free"), FakeLLM("smart")
    t = make(free, None, smart, pinned="fast")  # fast missing -> nearest configured above it
    assert t.complete("s", user("open safari"), TOOLS).text == "smart says hi"
    assert t.last_route.pinned and "pinned" in str(t.last_route)
    t2 = make(free, None, None)  # only free configured: everything goes there
    assert t2.complete("s", user("compare the pros and cons of buying versus leasing a horse trailer"), TOOLS).text == "free says hi"


def test_shared_instances_are_not_called_twice():
    shared = FakeLLM("shared", fail=True)
    t = make(shared, shared, shared)
    with pytest.raises(LLMError):
        t.complete("s", user("open safari"), TOOLS)
    assert shared.calls == 1


def test_describe_and_router_integration(settings):
    free, fast, smart = KeywordBrain(), FakeLLM("fast"), FakeLLM("smart")
    t = make(free, fast, smart)
    assert t.describe().startswith("tiered [auto]: free=keyword, fast=fast (fast-model), smart=smart (smart-model)")
    router = Router(t, load_default_skills(), lambda: "SYS", SkillContext(settings=settings))
    assert router.ask("what time is it").startswith("It is")
    assert t.last_route.tier == "free"
    assert router.ask("write an email to the farrier asking to move Tuesday to Thursday") == "smart says hi"
    assert t.last_route.tier == "smart"
    assert t.requires_no_network if hasattr(t, "requires_no_network") else True


def test_falls_back_to_lower_tier_when_higher_tiers_fail():
    """A rated-fast request whose paid tiers are down still gets handled by the keyword rules."""
    free = KeywordBrain()
    fast, smart = FakeLLM("fast", fail=True), FakeLLM("smart", fail=True)
    t = make(free, fast, smart)
    # a chained request is rated for the fast tier (a rule would swallow the tail), but the rule still fires
    resp = t.complete("s", user("open safari and then open spotify"), TOOLS)
    assert fast.calls == 1 and smart.calls == 1
    assert resp.tool_calls and resp.tool_calls[0].name == "open_app"
    assert t.last_route.tier == "free" and t.last_route.escalated
    # but a keyword miss is not an answer: the real error surfaces
    with pytest.raises(LLMError) as e:
        t.complete("s", user("please write me a sonnet about the fog"), TOOLS)
    assert "fast" in str(e.value) and "smart" in str(e.value)
