"""Timers that speak when they finish."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Annotated

from .context import SkillContext
from .registry import skill

_UNITS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
}
_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45,
          "fifty": 50, "sixty": 60, "ninety": 90, "half": 0.5, "quarter": 0.25}


def _num(token: str) -> float:
    try:
        return float(token)
    except ValueError:
        return float(_WORDS.get(token, 0))


def parse_duration(text: str) -> int:
    """'5 minutes', '1 hour 30 minutes', 'ninety seconds', 'half an hour' -> seconds. 0 if unparseable."""
    t = text.lower().replace(",", " ")
    t = re.sub(r"\bhalf an hour\b", "30 minutes", t)
    t = re.sub(r"\bquarter of an hour\b", "15 minutes", t)
    t = re.sub(r"\b([\w.-]+)\s+and\s+a\s+half\s+(seconds?|minutes?|hours?)\b",
               lambda m: f"{_num(m.group(1)) + 0.5} {m.group(2)}", t)
    t = t.replace(" and ", " ")
    total = 0.0
    for m in re.finditer(r"([\d.]+|[a-z-]+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b", t):
        total += _num(m.group(1)) * _UNITS[m.group(2)]
    if total == 0:
        m = re.search(r"(\d+):(\d\d)", t)  # mm:ss
        if m:
            total = int(m.group(1)) * 60 + int(m.group(2))
    return int(round(total))


@dataclass
class _Timer:
    label: str
    seconds: int
    started: float
    thread: threading.Timer
    done: bool = False


@dataclass
class TimerBook:
    timers: list[_Timer] = field(default_factory=list)


_book = TimerBook()


def _humanize(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if s or not parts:
        parts.append(f"{s} second{'s' if s != 1 else ''}")
    return " ".join(parts)


@skill()
def set_timer(
    duration: Annotated[str, "How long, e.g. '5 minutes', '90 seconds', '1 hour 15 minutes'."],
    label: Annotated[str, "Optional name for the timer, e.g. 'eggs'."] = "",
    ctx: SkillContext = None,
) -> str:
    """Start a countdown timer; the assistant announces when it ends."""
    seconds = parse_duration(duration)
    if seconds <= 0:
        return f"Error: I couldn't understand the duration '{duration}'."
    name = label.strip() or "timer"

    def fire() -> None:
        t.done = True
        ctx.notify(f"{name.capitalize()} is done. {_humanize(seconds)} are up.")

    thread = threading.Timer(seconds, fire)
    thread.daemon = True
    t = _Timer(label=name, seconds=seconds, started=time.time(), thread=thread)
    _book.timers.append(t)
    thread.start()
    return f"{name.capitalize()} set for {_humanize(seconds)}."


@skill()
def list_timers() -> str:
    """List running timers and their remaining time."""
    active = [t for t in _book.timers if not t.done]
    if not active:
        return "No timers are running."
    parts = []
    for t in active:
        remaining = max(0, int(t.seconds - (time.time() - t.started)))
        parts.append(f"{t.label}: {_humanize(remaining)} left")
    return "Timers: " + "; ".join(parts) + "."


@skill()
def cancel_timers() -> str:
    """Cancel all running timers."""
    n = 0
    for t in _book.timers:
        if not t.done:
            t.thread.cancel()
            t.done = True
            n += 1
    _book.timers.clear()
    return f"Cancelled {n} timer{'s' if n != 1 else ''}." if n else "No timers were running."
