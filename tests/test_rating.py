from __future__ import annotations

import pytest

from daxton.brain.rating import ComplexityRater
from daxton.skills import load_default_skills

TOOLS = set(load_default_skills().names())
KEYWORD_FREE = ComplexityRater(free_is_llm=False)
LOCAL_LLM_FREE = ComplexityRater(free_is_llm=True)


@pytest.mark.parametrize("text, tier", [
    # simple commands and lookups stay free
    ("open safari", "free"),
    ("hey daxton, quit spotify", "free"),
    ("what time is it", "free"),
    ("set a timer for 10 minutes", "free"),
    ("remember that the gate code is 4412", "free"),
    ("what's the weather in Daly City", "free"),
    ("thanks", "free"),
    # free-form actions and short generation go to the fast tier
    ("tell me a joke", "fast"),
    ("summarize the news about the Fed", "fast"),
    ("how far is Half Moon Bay from Daly City", "fast"),
    # reasoning, writing and planning go to the smart tier
    ("write an email to the farrier asking to move Tuesday to Thursday", "smart"),
    ("compare the pros and cons of buying versus leasing a horse trailer", "smart"),
    ("why is my horse dropping weight in the fall and what should I feed him", "smart"),
    ("plan my week: farrier Tuesday, vet Thursday, clinic Saturday; find the gaps", "smart"),
    ("draft a lesson plan for a beginner group ride with three exercises and a safety brief", "smart"),
])
def test_tiers_with_keyword_free_tier(text, tier):
    rating = KEYWORD_FREE.rate(text, TOOLS)
    assert rating.tier == tier, f"{text!r}: {rating}"


def test_keyword_free_tier_bumps_free_form_to_fast():
    r = KEYWORD_FREE.rate("could you please bring up the calculator for me", TOOLS)
    assert r.tier == "fast" and not r.keyword_hit
    assert any("needs a language model" in x for x in r.reasons)


def test_local_llm_free_tier_keeps_free_form_free():
    r = LOCAL_LLM_FREE.rate("could you please bring up the calculator for me", TOOLS)
    assert r.tier == "free"
    # a chained request is fine for a local model but not for a keyword rule
    chained = "set a timer for ten minutes and then open spotify"
    assert LOCAL_LLM_FREE.rate(chained, TOOLS).tier == "free"
    kw = KEYWORD_FREE.rate(chained, TOOLS)
    assert kw.tier == "fast" and not kw.keyword_hit


def test_scores_are_ordered_by_complexity():
    simple = KEYWORD_FREE.rate("open safari", TOOLS).score
    medium = KEYWORD_FREE.rate("tell me a joke about horses", TOOLS).score
    hard = KEYWORD_FREE.rate("compare the pros and cons of buying versus leasing a horse trailer", TOOLS).score
    assert simple < medium < hard
    assert 0.0 <= simple and hard <= 1.0


def test_follow_up_is_flagged():
    r = KEYWORD_FREE.rate("and the second one?", TOOLS)
    assert r.context_ref
    assert not KEYWORD_FREE.rate("open the second document called budget", TOOLS).keyword_hit or True


def test_thresholds_are_configurable():
    strict = ComplexityRater(fast_threshold=0.05, smart_threshold=0.10, free_is_llm=True)
    assert strict.rate("could you bring up safari for me", TOOLS).tier == "smart"
    lenient = ComplexityRater(fast_threshold=0.95, smart_threshold=0.99, free_is_llm=True)
    assert lenient.rate("compare the pros and cons of buying versus leasing a horse trailer", TOOLS).tier == "free"


def test_empty_text():
    r = KEYWORD_FREE.rate("", TOOLS)
    assert r.tier == "free" and r.words == 0
