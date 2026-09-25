from __future__ import annotations

import pytest

from jarvis.brain.base import Message, ToolSpec
from jarvis.brain.keyword_brain import KeywordBrain
from jarvis.skills import load_default_skills

TOOLS = [ToolSpec(n, "", {"type": "object", "properties": {}}) for n in load_default_skills().names()]


def route(text: str):
    resp = KeywordBrain().complete("", [Message("user", text)], TOOLS)
    if resp.tool_calls:
        tc = resp.tool_calls[0]
        return tc.name, tc.arguments
    return "text", resp.text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("open safari", ("open_app", {"name": "safari"})),
        ("Jarvis, open Spotify please", ("open_app", {"name": "Spotify"})),
        ("launch the terminal app", ("open_app", {"name": "terminal"})),
        ("open youtube", ("open_website", {"name": "youtube"})),
        ("open the youtube website", ("open_website", {"name": "youtube website"})),
        ("go to github", ("open_website", {"name": "github"})),
        ("open google.com", ("open_website", {"name": "google.com"})),
        ("quit spotify", ("quit_app", {"name": "spotify"})),
        ("close the calculator app", ("quit_app", {"name": "calculator"})),
        ("search for horse trailers under 10k", ("web_search", {"query": "horse trailers under 10k"})),
        ("google Buck Brannaman clinic dates", ("web_search", {"query": "Buck Brannaman clinic dates"})),
        ("look up the weather in Daly City", ("web_search", {"query": "the weather in Daly City"})),
        ("find the file called budget 2026", ("find_files", {"query": "budget 2026"})),
        ("play cowboy dressage on youtube", ("youtube_search", {"query": "cowboy dressage"})),
        ("what time is it", ("current_datetime", {})),
        ("what's the time", ("current_datetime", {})),
        ("what is the date today", ("current_datetime", {})),
        ("what day is it", ("current_datetime", {})),
        ("who is Buck Brannaman", ("wikipedia_summary", {"topic": "Buck Brannaman"})),
        ("what is a hackamore", ("wikipedia_summary", {"topic": "hackamore"})),
        ("tell me about Daly City", ("wikipedia_summary", {"topic": "Daly City"})),
        ("set a timer for 5 minutes", ("set_timer", {"duration": "5 minutes"})),
        ("start timer 90 seconds", ("set_timer", {"duration": "90 seconds"})),
        ("remember that the gate code is 4412", ("remember", {"note": "the gate code is 4412"})),
        ("what did I ask you to remember", ("recall_notes", {})),
        ("set the volume to 30 percent", ("set_volume", {"level": 30})),
        ("volume 80", ("set_volume", {"level": 80})),
        ("mute", ("set_volume", {"level": 0})),
        ("battery", ("battery_status", {})),
        ("take a screenshot", ("take_screenshot", {})),
        ("news about horses", ("news_search", {"query": "horses"})),
        ("new conversation", ("new_conversation", {})),
        ("goodbye", ("end_session", {})),
        ("stop listening", ("end_session", {})),
    ],
)
def test_rules(text, expected):
    assert route(text) == expected


def test_unknown_and_help():
    kind, text = route("please write me a sonnet")
    assert kind == "text" and "didn't catch" in text
    kind, text = route("help")
    assert kind == "text" and "keyword mode" in text
    kind, text = route("hello jarvis")
    assert kind == "text" and text.startswith("Hello")


def test_tool_result_is_spoken_verbatim():
    msgs = [Message("user", "what time is it"), Message("assistant", "", []), Message("tool", "It is noon.", "kw_1", "current_datetime")]
    resp = KeywordBrain().complete("", msgs, TOOLS)
    assert resp.text == "It is noon." and not resp.tool_calls


def test_only_available_tools_are_used():
    resp = KeywordBrain().complete("", [Message("user", "open safari")], [])
    assert not resp.tool_calls


def test_timer_label_is_extracted():
    assert route("set a timer for 12 minutes for the eggs") == ("set_timer", {"duration": "12 minutes", "label": "eggs"})
    assert route("start timer 1 hour 15 minutes") == ("set_timer", {"duration": "1 hour 15 minutes"})
