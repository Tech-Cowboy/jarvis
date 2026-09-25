"""Complexity rating: how much brain does this request need?

The rater is deterministic and runs in microseconds (no model call), so it never
adds latency. It scores the request 0..1 from cheap linguistic signals and maps
the score to a tier:

    free   simple commands, lookups, small talk        -> keyword rules or a local model
    fast   free-form actions, factual questions, short summaries -> a small hosted model
    smart  reasoning, comparison, writing, multi-step plans, long or subtle requests

`jarvis rate "..."` prints the score and the reasons so the thresholds can be tuned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .keyword_brain import find_rule, find_small_talk, strip_name

TIERS = ("free", "fast", "smart")

_REASONING = re.compile(
    r"\b(why|how (?:do|does|did|would|should|can|could|might)|explain|compare|comparison|difference|differences|"
    r"versus|vs\.?|pros and cons|pro and con|advantages?|disadvantages?|analy[sz]e|analysis|evaluate|assess|"
    r"recommend|recommendation|should i|should we|which (?:is|one is|would be) (?:better|best)|best way|strategy|"
    r"plan (?:for|to|out|my|the|a)|trade-?offs?|think through|reason(?:ing)?|walk me through|step by step|"
    r"in detail|thoroughly|outline|prioriti[sz]e|estimate|forecast|predict|diagnose|troubleshoot|debug|"
    r"what would happen|what if|figure out|work out|decide|decision)\b", re.I)
_GENERATION = re.compile(
    r"\b(write|draft|compose|rewrite|rephrase|reword|edit|proofread|translate|summari[sz]e|summary|"
    r"brainstorm|ideas?|names? for|slogan|tagline|caption|joke|riddle|haiku|limerick|list of|describe|"
    r"generate|create|make (?:me )?(?:a|an)|come up with)\b", re.I)
_ARTIFACT = re.compile(
    r"\b(email|e-mail|letter|essay|story|poem|speech|toast|script|blog|article|post|memo|report|proposal|"
    r"contract|agreement|policy|curriculum|lesson plan|outline|resume|cover letter|bio|code|function|class|"
    r"regex|sql|query|formula|spreadsheet|table|schedule|itinerary|budget)\b", re.I)
_MULTISTEP = re.compile(r"\b(and then|then|after that|afterwards|next|first|second|third|finally|before you|"
                        r"once you|for each|every one|both|all of them)\b|;", re.I)
_MATH = re.compile(r"\d+\s*(?:[-+*/x×÷%]|percent|plus|minus|times|divided by)\s*\d+|"
                   r"\b(calculate|compute|convert|how much (?:is|would|does)|how many .* (?:in|per)|sum|total|"
                   r"average|multiply|divide|square root|interest|tip|split the bill|per month|per year)\b", re.I)
_CONTEXT_REF = re.compile(r"\b(that one|those|them|the (?:first|second|third|last|previous|other|same) one|again|"
                          r"earlier|previous|previously|last time|you (?:said|found|mentioned|opened|suggested)|"
                          r"instead|also|too|what about|and (?:the )?other)\b", re.I)
_FACTUAL = re.compile(r"^(?:what|who|whom|when|where|which|how (?:old|far|tall|big|many|much|long|late|early))\b", re.I)
_SIMPLE_ACTION = re.compile(
    r"^(?:open|launch|start|run|quit|close|play|pause|stop|mute|unmute|set|turn|show|find|search|google|look up|"
    r"remember|note|recall|take|screenshot|volume|battery|time|date|timer|goodbye|bye|exit|hello|hi|hey|thanks|"
    r"thank you|what time|what's the time|what day|what's the date)\b", re.I)


@dataclass
class Rating:
    score: float
    tier: str
    reasons: list[str] = field(default_factory=list)
    words: int = 0
    context_ref: bool = False  # short follow-up that leans on the previous turn
    keyword_hit: bool = False  # a classic keyword rule handles it outright

    def __str__(self) -> str:
        return f"{self.score:.2f} -> {self.tier} ({'; '.join(self.reasons) or 'no cues'})"


class ComplexityRater:
    def __init__(self, fast_threshold: float = 0.30, smart_threshold: float = 0.60,
                 free_is_llm: bool = False, assistant_name: str = "Jarvis"):
        self.fast_threshold = fast_threshold
        self.smart_threshold = smart_threshold
        self.free_is_llm = free_is_llm  # a local model at the free tier can take free-form requests
        self.assistant_name = assistant_name

    def rate(self, text: str, available_tools: set[str] | None = None) -> Rating:
        t = strip_name(text or "", self.assistant_name)
        words = len(t.split())
        reasons: list[str] = []
        score = 0.08 + min(words, 60) * 0.006
        reasons.append(f"{words} word{'s' if words != 1 else ''}")

        reasoning = _uniq(_REASONING.findall(t))
        if reasoning:
            score += 0.30 + 0.15 * min(len(reasoning) - 1, 2)
            reasons.append("reasoning: " + ", ".join(reasoning))
            if words >= 12:
                score += 0.15
                reasons.append("long reasoning question")
        generation = _uniq(_GENERATION.findall(t))
        if generation:
            score += 0.35
            reasons.append("writing: " + ", ".join(generation))
        artifact = _uniq(_ARTIFACT.findall(t))
        if artifact and (generation or reasoning or words >= 8):
            score += 0.15
            reasons.append("produces: " + ", ".join(artifact))
        steps = _MULTISTEP.findall(t)
        if steps:
            score += min(0.25, 0.12 * len(steps))
            reasons.append(f"multi-step ({len(steps)} cue{'s' if len(steps) != 1 else ''})")
        if _MATH.search(t):
            score += 0.15
            reasons.append("math")
        sentences = len([s for s in re.split(r"[.!?]+", t) if s.strip()])
        if sentences > 1:
            score += min(0.15, 0.05 * (sentences - 1))
            reasons.append(f"{sentences} sentences")
        context_ref = bool(_CONTEXT_REF.search(t)) and words <= 12
        if context_ref:
            score += 0.05
            reasons.append("follow-up (refers to earlier context)")
        factual = bool(_FACTUAL.match(t))
        if factual and not reasoning and not generation:
            reasons.append("factual question")

        keyword_hit = find_rule(t, available_tools) is not None or find_small_talk(t) is not None
        if keyword_hit and steps:
            keyword_hit = False  # a rule would swallow the rest of a chained request ("... and then open spotify")
            reasons.append("chained request, too much for a keyword rule")
        if _SIMPLE_ACTION.match(t) and words <= 8 and not reasoning and not generation:
            score = min(score, 0.15)
            reasons.append("simple command")
        if keyword_hit:
            score = min(score, 0.05)
            reasons.append("keyword rule matches")
        elif not self.free_is_llm and words > 0:
            # keyword mode cannot take anything free-form: bump to the first real model
            score = max(score, self.fast_threshold)
            reasons.append("needs a language model (free tier is keyword-only)")

        score = max(0.0, min(1.0, score))
        return Rating(score=round(score, 3), tier=self.tier_for(score), reasons=reasons, words=words,
                      context_ref=context_ref, keyword_hit=keyword_hit)

    def tier_for(self, score: float) -> str:
        if score < self.fast_threshold:
            return "free"
        if score < self.smart_threshold:
            return "fast"
        return "smart"


def _uniq(items: list) -> list[str]:
    out: list[str] = []
    for it in items:
        s = (it if isinstance(it, str) else next((x for x in it if x), "")).strip().lower()
        if s and s not in out:
            out.append(s)
    return out
