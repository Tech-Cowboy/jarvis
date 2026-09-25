"""What a skill may reach: settings, the voice, and session control."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from ..config import Settings


@dataclass
class SkillContext:
    settings: Settings
    speak: Callable[[str], None] = lambda text: print(text)  # replaced by the assistant's speaker
    stop_event: threading.Event = field(default_factory=threading.Event)  # set to end the session
    reset_conversation: Callable[[], None] = lambda: None  # replaced by the router

    def notify(self, text: str) -> None:
        """Speak from a background thread (timers). Falls back to print if speaking fails."""
        try:
            self.speak(text)
        except Exception:
            print(text)
