from __future__ import annotations

from jarvis.assistant import Assistant
from jarvis.brain.keyword_brain import KeywordBrain
from jarvis.doctor import format_checks, run_checks
from jarvis.tts.console import ConsoleSpeaker


def make_assistant(settings):
    return Assistant(settings, KeywordBrain(), ConsoleSpeaker())


def test_handle_runs_a_skill_end_to_end(settings, capsys):
    a = make_assistant(settings)
    reply = a.handle("remember that the farrier comes Tuesday")
    assert reply == "Noted: the farrier comes Tuesday."
    assert "↳ remember(note='the farrier comes Tuesday')" in capsys.readouterr().out
    assert a.handle("what did I ask you to remember").startswith("You have 1 note.")


def test_goodbye_sets_stop_event(settings):
    a = make_assistant(settings)
    assert a.handle("goodbye") == "Goodbye."
    assert a.stop_event.is_set()


def test_chat_mode_reads_until_exit(settings, monkeypatch, capsys):
    a = make_assistant(settings)
    lines = iter(["what time is it", "", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))
    a.run_chat(speak=True)
    out = capsys.readouterr().out
    assert "It is" in out and a.speaker.spoken and a.speaker.spoken[0].startswith("It is")


def test_strip_name(settings):
    a = make_assistant(settings)
    assert a._strip_name("Hey Jarvis, open Safari") == "open Safari"
    assert a._strip_name("jarvis") == ""
    assert a._strip_name("what time is it jarvis?") == "what time is it"


def test_doctor_reports_and_formats(settings, monkeypatch):
    monkeypatch.setattr("jarvis.config._ollama_reachable", lambda host: False)
    checks = run_checks(settings, online=False)
    areas = {c.area for c in checks}
    assert {"python", "config", "brain", "voice", "ears", "wake", "web"} <= areas
    text = format_checks(checks)
    assert "[OK  ] python" in text and ("problem" in text or "All good" in text)


def test_chat_mode_catches_terminal_commands(settings, monkeypatch, capsys):
    from jarvis.assistant import _looks_like_cli_command

    assert _looks_like_cli_command("jarvis listen") and _looks_like_cli_command("jarvis") \
        and _looks_like_cli_command("jarvis doctor --online") and _looks_like_cli_command("jarvis -v")
    assert not _looks_like_cli_command("jarvis, open safari") and not _looks_like_cli_command("jarvis what time is it")
    a = make_assistant(settings)
    lines = iter(["jarvis listen", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))
    a.run_chat()
    assert "terminal command" in capsys.readouterr().out
