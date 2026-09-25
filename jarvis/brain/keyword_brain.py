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


def _rules() -> list[Rule]:
    r = re.compile
    return [
        Rule(r(r"^(?:new conversation|forget (?:that|this)|start over|clear (?:the )?history)[.!?]*$", re.I),
             lambda m: ("new_conversation", {})),
        Rule(r(r"^(?:goodbye|good bye|bye|exit|quit|stop listening|shut ?down|go to sleep|that(?:'s| is) all)[.!?]*$", re.I),
             lambda m: ("end_session", {})),
        Rule(r(r"^(?:set|start)\s+(?:a\s+)?(?:timer|alarm)\s+(?:for\s+)?(?P<d>.+?)[.!?]*$", re.I),
             lambda m: ("set_timer", {"duration": m["d"]})),
        Rule(r(r"^(?:jarvis[,]?\s*)?(?:please\s+)?(?:can you\s+)?(?:go to|open|launch|start|run)\s+(?:the\s+)?(?:app\s+|application\s+|website\s+|site\s+)?(?P<name>.+?)(?:\s+(?:app|application|for me|please))?[.!?]*$", re.I),
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
             lambda m: ("wikipedia_summary", {"topic": m["t"]})),
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


class KeywordBrain(LLM):
    name = "keyword"

    def __init__(self, assistant_name: str = "Jarvis"):
        self.assistant_name = assistant_name
        self._rules = _rules()
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

        text = _strip_name(last.content, self.assistant_name)
        available = {t.name for t in tools}
        for rule in self._rules:
            m = rule.pattern.match(text)
            if not m:
                continue
            name, args = rule.build(m)
            if name not in available:
                continue
            self._counter += 1
            return LLMResponse(tool_calls=[ToolCall(id=f"kw_{self._counter}", name=name, arguments=args)])
        if re.search(r"\b(hello|hi|hey|good (morning|afternoon|evening))\b", text, re.I):
            return LLMResponse(text="Hello. What can I do for you?")
        if re.search(r"\b(help|what can you do)\b", text, re.I):
            return LLMResponse(text=HELP)
        return LLMResponse(text="I didn't catch a command I know. Say help to hear what I can do.")


def _strip_name(text: str, assistant_name: str) -> str:
    text = text.strip()
    text = re.sub(rf"^(?:hey|ok|okay|hi)?[,\s]*{re.escape(assistant_name)}[,.!\s]*", "", text, flags=re.I)
    return text.strip()
