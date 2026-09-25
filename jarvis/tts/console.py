"""No audio: print what would be spoken. Used by `jarvis chat` and the tests."""

from __future__ import annotations

from . import Speaker, clean_for_speech


class ConsoleSpeaker(Speaker):
    name = "console"

    def __init__(self, assistant_name: str = "Jarvis"):
        self.assistant_name = assistant_name
        self.spoken: list[str] = []

    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        self.spoken.append(text)
        print(f"{self.assistant_name}: {text}", flush=True)
