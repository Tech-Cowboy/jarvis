from __future__ import annotations

import platform
import re
import time

import pytest

from jarvis.skills import apps, files, notes, system, timers, web
from jarvis.skills.context import SkillContext


# ----------------------------------------------------------------- apps
@pytest.mark.parametrize("spoken, expected", [
    ("chrome", "Google Chrome"),
    ("VS Code", "Visual Studio Code"),
    ("the terminal app", "Terminal"),
    ("Spotify please", "Spotify"),
    ("System Preferences", "System Settings"),
])
def test_resolve_app_aliases(spoken, expected):
    assert apps.resolve_app_name(spoken) == expected


def test_unknown_app_passes_through():
    assert apps.resolve_app_name("Some Unknown Thing") == "Some Unknown Thing"


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
def test_find_installed_apps_on_mac():
    assert "Finder" in apps.find_installed_apps("finder") or apps.find_installed_apps("safari")


# ------------------------------------------------------------------ web
@pytest.mark.parametrize("name, url", [
    ("youtube", "https://www.youtube.com"),
    ("the youtube website", "https://www.youtube.com"),
    ("github.com", "https://github.com"),
    ("https://example.org/x", "https://example.org/x"),
    ("cowboy dressage", "https://duckduckgo.com/?q=cowboy+dressage"),
])
def test_resolve_site(name, url):
    assert web.resolve_site(name) == url


def test_open_website_uses_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(web.webbrowser, "open", lambda u: opened.append(u) or True)
    assert web.open_website("github") == "Opened github in your browser."
    assert opened == ["https://github.com"]
    assert "YouTube" in web.youtube_search("horse clinics")
    assert opened[-1].endswith("search_query=horse+clinics")


def test_web_search_formats_results(monkeypatch):
    class FakeDDGS:
        def text(self, query, max_results=5):
            return [{"title": "Ocean View Stables", "href": "https://www.oceanviewstables.com/", "body": "Beach  rides."}]

    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    out = web.web_search("ocean view stables", max_results=3)
    assert out.startswith("Search results for 'ocean view stables':")
    assert "1. Ocean View Stables (oceanviewstables.com): Beach rides." in out


def test_web_search_error_is_a_string(monkeypatch):
    class Broken:
        def text(self, *a, **k):
            raise RuntimeError("offline")

    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", Broken)
    assert web.web_search("x").startswith("Error: web search failed")


def test_wikipedia_falls_back_to_search(monkeypatch):
    def boom(title):
        raise RuntimeError("429")

    monkeypatch.setattr(web, "_wikipedia_extract", boom)
    monkeypatch.setattr(web, "web_search", lambda q, max_results=5: f"SEARCH({q})")
    out = web.wikipedia_summary("Buck Brannaman")
    assert out.startswith("Wikipedia was unavailable") and "SEARCH(Buck Brannaman)" in out


def test_wikipedia_summary_limits_to_three_sentences(monkeypatch):
    monkeypatch.setattr(web, "_wikipedia_extract", lambda t: ("Horse", "One. Two. Three. Four. Five."))
    assert web.wikipedia_summary("horse") == "Horse: One. Two. Three."


# --------------------------------------------------------------- system
def test_current_datetime_format():
    out = system.current_datetime()
    assert re.match(r"It is \d{1,2}:\d\d (AM|PM) on \w+, \w+ \d\d, \d{4}\.", out)


# ---------------------------------------------------------------- notes
def test_notes_roundtrip(settings):
    ctx = SkillContext(settings=settings)
    assert notes.recall_notes(ctx=ctx) == "You have no saved notes."
    assert notes.remember("the gate code is 4412", ctx=ctx) == "Noted: the gate code is 4412."
    notes.remember("farrier comes Tuesday", ctx=ctx)
    out = notes.recall_notes(ctx=ctx)
    assert out.startswith("You have 2 notes.") and "gate code" in out and "farrier" in out
    assert notes.recall_notes("farrier", ctx=ctx).startswith("You have 1 note.")
    assert notes.forget_notes("gate", ctx=ctx) == "Deleted 1 note."
    assert notes.forget_notes("all", ctx=ctx) == "Deleted all 1 notes."
    assert (settings.data_dir / "notes.json").is_file()


# --------------------------------------------------------------- timers
@pytest.mark.parametrize("text, seconds", [
    ("5 minutes", 300), ("90 seconds", 90), ("1 hour 15 minutes", 4500), ("ninety seconds", 90),
    ("half an hour", 1800), ("two and a half minutes", 150), ("2:30", 150), ("a minute", 60), ("nonsense", 0),
])
def test_parse_duration(text, seconds):
    assert timers.parse_duration(text) == seconds


def test_timer_fires_and_notifies(settings):
    spoken = []
    ctx = SkillContext(settings=settings, speak=spoken.append)
    assert timers.set_timer("1 second", label="eggs", ctx=ctx) == "Eggs set for 1 second."
    assert "eggs" in timers.list_timers()
    time.sleep(1.4)
    assert spoken and spoken[0].startswith("Eggs is done.")
    assert timers.list_timers() == "No timers are running."
    assert timers.set_timer("banana", ctx=ctx).startswith("Error")
    timers.set_timer("10 minutes", ctx=ctx)
    assert timers.cancel_timers() == "Cancelled 1 timer."


# ---------------------------------------------------------------- files
def test_find_files_with_find(monkeypatch, tmp_path):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "Budget 2026.xlsx").write_text("x")
    monkeypatch.setattr(files.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(files.platform, "system", lambda: "Linux")
    out = files.find_files("budget")
    assert out.startswith("Found 1 matching 'budget': ~/Documents/Budget 2026.xlsx")
    assert files.find_files("zzz-nothing") == "I didn't find any files matching 'zzz-nothing'."
