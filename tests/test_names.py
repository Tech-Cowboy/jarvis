from __future__ import annotations

import pytest

from daxton.config import Settings
from daxton.wake.names import contains_name, find_name, strip_name


@pytest.mark.parametrize("text", [
    "Daxton, open Safari", "hey daxton what time is it", "Dexton open spotify", "Daxon, set a timer",
    "Daxten what's the weather", "could you open safari, Daxton?",
])
def test_name_is_found_despite_spellings(text):
    assert contains_name(text, "Daxton")


@pytest.mark.parametrize("text", ["open safari", "Dexter, open safari", "Baxter what time is it", "the taxon is wrong", "dax"])
def test_unrelated_words_do_not_trigger(text):
    assert not contains_name(text, "Daxton")


def test_aliases_extend_matching():
    assert contains_name("Dax, open safari", "Daxton", aliases=["Dax"])
    assert strip_name("Dax, open safari", "Daxton", aliases=["Dax"]) == "open safari"


@pytest.mark.parametrize("text, expected", [
    ("Hey Daxton, open Safari", "open Safari"),
    ("daxton", ""),
    ("what time is it daxton?", "what time is it"),
    ("Dexton set a timer for five minutes", "set a timer for five minutes"),
    ("okay Daxton, what's the weather", "what's the weather"),
])
def test_strip_name(text, expected):
    assert strip_name(text, "Daxton") == expected


def test_find_name_returns_span():
    m = find_name("Hey Daxton, open Safari", "Daxton")
    assert m and m.group(0) == "Daxton"


def test_wake_resolution_follows_the_name(monkeypatch):
    s = Settings(assistant_name="Daxton")
    assert s.resolved_wake_word() == ""
    assert s.resolved_wake_mode() == "name"
    assert s.wake_phrase == "Daxton"
    s_custom = Settings(assistant_name="Daxton", wake_word="/models/hey_daxton.onnx")
    assert s_custom.resolved_wake_word() == "/models/hey_daxton.onnx"
    j = Settings(assistant_name="Jarvis")
    pytest.importorskip("openwakeword")
    assert j.resolved_wake_word() == "hey_jarvis" and j.resolved_wake_mode() == "wakeword" and j.wake_phrase == "hey jarvis"
    assert s_custom.wake_phrase == "hey daxton"
    assert Settings(assistant_aliases="Dax, Dexton").aliases == ["Dax", "Dexton"]
