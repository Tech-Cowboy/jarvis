"""Wake-word detection: openWakeWord ("hey jarvis"), push-to-talk, or name-in-transcript."""

from __future__ import annotations

from abc import ABC, abstractmethod


def talk_requested(talk_event) -> bool:
    """True (and clears the event) when the dashboard's push-to-talk asked to listen now."""
    if talk_event is not None and talk_event.is_set():
        talk_event.clear()
        return True
    return False


class WakeDetector(ABC):
    name: str = "wake"

    @abstractmethod
    def wait(self, stop_event, talk_event=None) -> bool:
        """Block until the assistant should listen. Returns False if the session was stopped.

        `talk_event` is an optional threading.Event set by the dashboard's push-to-talk; a detector
        returns True (and clears it) as soon as it is set.
        """
        raise NotImplementedError

    def describe(self) -> str:
        return self.name
