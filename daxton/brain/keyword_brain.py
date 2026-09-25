"""The classic no-API brain: keyword and pattern matching.

This is what the original Python JARVIS tutorials did before LLMs existed.
It is the fallback when no model provider is configured, and it is also a
deterministic brain for tests. It implements the same `LLM` interface, so the
router does not know the difference: it "calls a tool" by pattern, then speaks
the tool's result verbatim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .base import LLM, LLMResponse, Message, ToolCall, ToolSpec

WEBSITES = {"youtube", "google", "gmail", "github", "reddit", "wikipedia", "amazon", "netflix", "twitter", "x",
            "facebook", "instagram", "linkedin", "maps", "weather", "spotify web", "chatgpt", "claude", "odoo"}


@dataclass
class Rule:
    pattern: re.Pattern
    build: Callable[[re.Match], tuple[str, dict]]


_TIME_SENSITIVE = re.compile(r"\b(weather|forecast|news|price|prices|cost|stock|score|scores|today|tonight|tomorrow|now|"
                             r"latest|current|currently|open (?:right )?now|hours|traffic|schedule)\b", re.I)


def _lookup(topic: str) -> tuple[str, dict]:
    """'who is X' -> Wikipedia; anything time-sensitive ('what's the weather today') -> web search."""
    if _TIME_SENSITIVE.search(topic):
        return "web_search", {"query": topic}
    return "wikipedia_summary", {"topic": topic}


def _rules() -> list[Rule]:
    r = re.compile
    return [
        Rule(r(r"^(?:new conversation|forget (?:that|this)|start over|clear (?:the )?history)[.!?]*$", re.I),
             lambda m: ("new_conversation", {})),
        Rule(r(r"^(?:goodbye|good bye|bye|exit|quit|stop listening|shut ?down|go to sleep|that(?:'s| is) all)[.!?]*$", re.I),
             lambda m: ("end_session", {})),
        Rule(r(r"^(?:set|start)\s+(?:a\s+)?(?:timer|alarm)\s+(?:for\s+)?(?P<d>.+?)(?:\s+(?:for|called|named)\s+(?:the\s+|my\s+)?(?P<label>[^.!?]+?))?[.!?]*$", re.I),
             lambda m: ("set_timer", {"duration": m["d"], **({"label": m["label"]} if m["label"] else {})})),
        Rule(r(r"^(?:(?:please|can you|could you|would you|will you|kindly)[,\s]+)*(?:go to|open|launch|start|run)\s+(?:the\s+)?(?:app\s+|application\s+|website\s+|site\s+)?(?P<name>.+?)(?:\s+(?:app|application|for me|please))?[.!?]*$", re.I),
             lambda m: (("open_website", {"name": m["name"]}) if m["name"].strip().lower() in WEBSITES
                        or "." in m["name"] or m["name"].lower().endswith((" website", " site", ".com"))
                        else ("open_app", {"name": m["name"]}))),
        Rule(r(r"^(?:quit|close|exit|kill|shut down|shutdown)\s+(?:the\s+)?(?:app\s+)?(?P<name>.+?)(?:\s+app)?[.!?]*$", re.I),
             lambda m: ("quit_app", {"name": m["name"]})),
        Rule(r(r"^(?:find|locate|search for|search)\s+(?:the\s+|a\s+|my\s+)?(?:file|files|document|documents|folder|folders)\s+(?:called\s+|named\s+|matching\s+)?(?P<q>.+?)[.!?]*$", re.I),
             lambda m: ("find_files", {"query": m["q"]})),
        Rule(r(r"^(?:search|google|look up|lookup|search for|search the web for|web search|find out)\s+(?:for\s+)?(?P<q>.+?)[.!?]*$", re.I),
             lambda m: ("web_search", {"query": m["q"]})),
        Rule(r(r"^(?:youtube|play|search youtube for|find on youtube)\s+(?P<q>.+?)(?:\s+on youtube)?[.!?]*$", re.I),
             lambda m: ("youtube_search", {"query": m["q"]})),
        Rule(r(r"^(?:what(?:'s|\s+is)?\s+)?(?:the\s+)?(?:current\s+)?(?:time|date|day)(?:\s+(?:is it|today|now|is it today|is today))?[.!?]*$", re.I),
             lambda m: ("current_datetime", {})),
        Rule(r(r"^(?:wikipedia|wiki|who is|who was|what is|what's|what are|tell me about|define)\s+(?:a |an |the )?(?P<t>.+?)[.!?]*$", re.I),
             lambda m: _lookup(m["t"])),
        Rule(r(r"^(?:remember|note|write down|take a note)\s+(?:that\s+)?(?P<n>.+?)[.!?]*$", re.I),
             lambda m: ("remember", {"note": m["n"]})),
        Rule(r(r"^(?:what did i (?:ask you to |tell you to )?remember|recall|read my notes|my notes|list notes)[.!?]*$", re.I),
             lambda m: ("recall_notes", {})),
        Rule(r(r"^(?:set\s+)?(?:the\s+)?volume\s+(?:to\s+)?(?P<n>\d{1,3})\s*(?:percent|%)?[.!?]*$", re.I),
             lambda m: ("set_volume", {"level": int(m["n"])})),
        Rule(r(r"^(?:mute|silence)(?: the (?:volume|sound|mac|computer))?[.!?]*$", re.I),
             lambda m: ("set_volume", {"level": 0})),
        Rule(r(r"^(?:battery|battery status|how(?:'s| is) (?:the|my) battery)[.!?]*$", re.I),
             lambda m: ("battery_status", {})),
        Rule(r(r"^(?:take a |grab a )?screenshot[.!?]*$", re.I),
             lambda m: ("take_screenshot", {})),
        Rule(r(r"^(?:news|headlines|what's in the news|latest news)(?:\s+(?:about|on)\s+(?P<q>.+?))?[.!?]*$", re.I),
             lambda m: ("news_search", {"query": m["q"] or "top news"})),
    ]


HELP = ("I'm running in classic keyword mode because no language model is configured. "
        "I can open or quit apps, open websites, search the web, look things up on Wikipedia, "
        "tell you the time, set timers, take notes, find files, set the volume, "
        "check the battery, take a screenshot, or read the news. "
        "Add an Anthropic, OpenAI or Ollama model in the dot env file for open-ended questions.")

MISS_TEXT = "I didn't catch a command I know. Say help to hear what I can do."

# Small talk the keyword brain answers itself (no tool, no model).
SMALL_TALK: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:hello|hi|hey|good (?:morning|afternoon|evening))\b.*$", re.I), "Hello. What can I do for you?"),
    (re.compile(r"^(?:thanks|thank you|cheers|appreciate it)\b.*$", re.I), "You're welcome."),
    (re.compile(r"^(?:ok|okay|alright|yes|yep|no|nope|never ?mind|cancel)\b.*$", re.I), "Alright."),
    (re.compile(r"^(?:help|what can you do)\b.*$", re.I), HELP),
]

_RULES = _rules()


def strip_name(text: str, assistant_name: str = "Daxton", aliases=()) -> str:
    """Drop the assistant's name (and a leading 'hey') wherever it appears, tolerant of STT spellings."""
    from ..wake.names import strip_name as _strip

    return _strip(text or "", assistant_name, aliases)


def find_rule(text: str, available: set[str] | None = None) -> tuple[str, dict] | None:
    """(tool name, arguments) for the first matching keyword rule, or None. Used by the complexity rater too."""
    for rule in _RULES:
        m = rule.pattern.match(text)
        if not m:
            continue
        name, args = rule.build(m)
        if available is not None and name not in available:
            continue
        return name, args
    return None


def find_small_talk(text: str) -> str | None:
    for pattern, reply in SMALL_TALK:
        if pattern.match(text):
            return reply
    return None


class KeywordBrain(LLM):
    name = "keyword"

    def __init__(self, assistant_name: str = "Daxton"):
        self.assistant_name = assistant_name
        self._counter = 0

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        if not messages:
            return LLMResponse(text=HELP)
        last = messages[-1]
        if last.role == "tool":
            # Speak the tool's result verbatim (join several results if there were several).
            results = []
            for m in reversed(messages):
                if m.role != "tool":
                    break
                results.append(m.content)
            return LLMResponse(text=" ".join(reversed(results)).strip() or "Done.")
        if last.role != "user":
            return LLMResponse(text=HELP)

        text = strip_name(last.content, self.assistant_name)
        hit = find_rule(text, {t.name for t in tools})
        if hit:
            name, args = hit
            self._counter += 1
            return LLMResponse(tool_calls=[ToolCall(id=f"kw_{self._counter}", name=name, arguments=args)])
        reply = find_small_talk(text)
        if reply:
            return LLMResponse(text=reply)
        return LLMResponse(text=MISS_TEXT, miss=True)


# kept for callers that imported the private name
_strip_name = strip_name
