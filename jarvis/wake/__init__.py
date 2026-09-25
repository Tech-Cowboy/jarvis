"""Wake-word detection: openWakeWord ("hey jarvis"), push-to-talk, or name-in-transcript."""

from __future__ import annotations

from abc import ABC, abstractmethod


class WakeDetector(ABC):
    name: str = "wake"

    @abstractmethod
    def wait(self, stop_event) -> bool:
        """Block until the assistant should listen. Returns False if the session was stopped."""
        raise NotImplementedError

    def describe(self) -> str:
        return self.name
