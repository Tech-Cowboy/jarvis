"""TieredLLM: rate each request, start at the cheapest tier that fits, escalate only when needed.

    free  -> fast -> smart

Routing happens once per user turn (the tool-calling loop keeps the same tier so
a turn is handled consistently). Escalation happens when a tier raises an
LLMError (offline, bad key, rate limit) or when the keyword brain reports a miss.
Short follow-ups ("and the second one?") inherit the previous turn's tier so a
conversation that needed the smart model does not fall back mid-thought.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

from .base import LLM, LLMError, LLMResponse, Message, ToolSpec
from .rating import TIERS, ComplexityRater, Rating

log = logging.getLogger(__name__)


@dataclass
class Route:
    tier: str
    llm: str
    rating: Rating | None
    escalated: bool = False
    pinned: bool = False

    def __str__(self) -> str:
        how = "pinned" if self.pinned else (f"score {self.rating.score:.2f}" if self.rating else "")
        if self.escalated:
            how = (how + ", " if how else "") + "escalated"
        return f"{self.tier} via {self.llm}" + (f" ({how})" if how else "")


class TieredLLM(LLM):
    name = "tiered"

    def __init__(self, tiers: dict[str, LLM | None], rater: ComplexityRater, escalate: bool = True,
                 pinned: str | None = None):
        self.tiers: list[LLM | None] = [tiers.get(t) for t in TIERS]
        if not any(self.tiers):
            raise LLMError("TieredLLM needs at least one configured tier.")
        self.rater = rater
        self.escalate = escalate
        self.pinned = pinned if pinned in TIERS else None
        self.stats: Counter[str] = Counter()
        self.last_route: Route | None = None
        self._turn_tier: int = 0  # tier index for the turn in progress
        self._turn_rating: Rating | None = None
        self._turn_open = False  # True until the first successful call of a turn is recorded
        self._last_turn_tier: int | None = None

    # ------------------------------------------------------------------ api
    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        if not messages or messages[-1].role == "user":
            self._start_turn(messages[-1].content if messages else "", tools)
        start = self._turn_tier
        errors: list[str] = []
        tried: set[int] = set()
        for i in range(start, len(TIERS)):
            llm = self.tiers[i]
            if llm is None or id(llm) in tried:
                continue
            tried.add(id(llm))
            try:
                resp = llm.complete(system, messages, tools)
            except LLMError as e:
                errors.append(f"{TIERS[i]} ({llm.describe()}): {e}")
                log.warning("tier %s failed: %s", TIERS[i], e)
                if not self.escalate:
                    raise
                continue
            if resp.miss and self.escalate and self._has_higher(i):
                log.info("tier %s could not handle the request; escalating", TIERS[i])
                continue
            self._record(i, llm, escalated=(i != start))
            return resp
        raise LLMError("; ".join(errors) or "No LLM tier could handle the request.")

    def describe(self) -> str:
        parts = [f"{name}={llm.describe()}" for name, llm in zip(TIERS, self.tiers) if llm is not None]
        mode = f"pinned {self.pinned}" if self.pinned else "auto"
        return f"tiered [{mode}]: " + ", ".join(parts)

    def summary(self) -> str:
        turns = {t: self.stats[f"turns:{t}"] for t in TIERS if self.stats[f"turns:{t}"]}
        if not turns:
            return "No requests routed yet."
        text = ", ".join(f"{t} {n}" for t, n in turns.items())
        esc = self.stats["escalations"]
        return f"Routed turns: {text}" + (f"; escalations {esc}" if esc else "") + "."

    # -------------------------------------------------------------- private
    def _start_turn(self, user_text: str, tools: list[ToolSpec]) -> None:
        self._turn_open = True
        if self.pinned:
            self._turn_rating = None
            self._turn_tier = self._nearest_configured(TIERS.index(self.pinned))
            return
        rating = self.rater.rate(user_text, {t.name for t in tools})
        idx = TIERS.index(rating.tier)
        if rating.context_ref and self._last_turn_tier is not None and self._last_turn_tier > idx:
            idx = self._last_turn_tier
            rating.reasons.append(f"sticks with previous tier ({TIERS[idx]})")
        self._turn_rating = rating
        self._turn_tier = self._nearest_configured(idx)

    def _nearest_configured(self, idx: int) -> int:
        """The requested tier, or the next configured one above it (or the highest configured one)."""
        for j in range(idx, len(TIERS)):
            if self.tiers[j] is not None:
                return j
        return max(j for j, llm in enumerate(self.tiers) if llm is not None)

    def _has_higher(self, i: int) -> bool:
        return any(self.tiers[j] is not None and self.tiers[j] is not self.tiers[i] for j in range(i + 1, len(TIERS)))

    def _record(self, i: int, llm: LLM, escalated: bool) -> None:
        self.stats["calls"] += 1
        self.stats[f"calls:{TIERS[i]}"] += 1
        if self._turn_open:
            self.stats[f"turns:{TIERS[i]}"] += 1
            if escalated:
                self.stats["escalations"] += 1
            self._turn_open = False
        self._turn_tier = i
        self._last_turn_tier = i
        self.last_route = Route(tier=TIERS[i], llm=llm.describe(), rating=self._turn_rating, escalated=escalated,
                                pinned=self.pinned is not None)
