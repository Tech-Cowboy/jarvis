"""Wake strategies that need no model: push-to-talk (Enter key) and "always listening"."""

from __future__ import annotations

import sys
import threading

from . import WakeDetector, talk_requested


class PushToTalk(WakeDetector):
    """Press Enter in the terminal to talk. Ctrl-C or 'q' + Enter to quit."""

    name = "push_to_talk"

    def wait(self, stop_event, talk_event=None) -> bool:
        result: dict[str, bool] = {}

        def reader() -> None:
            try:
                line = sys.stdin.readline()
            except Exception:
                line = ""
            result["quit"] = (line.strip().lower() in {"q", "quit", "exit"}) or line == ""

        print("\n[Press Enter to talk, 'q' + Enter to quit]", flush=True)
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        while t.is_alive():
            t.join(0.2)
            if stop_event.is_set():
                return False
            if talk_requested(talk_event):
                return True
        if result.get("quit"):
            stop_event.set()
            return False
        return True


class AlwaysListening(WakeDetector):
    """No wake word: the assistant listens continuously and the transcript must contain its name."""

    name = "name"

    def wait(self, stop_event, talk_event=None) -> bool:
        talk_requested(talk_event)
        return not stop_event.is_set()

    def describe(self) -> str:
        return "always listening (say the assistant's name in the sentence)"
