"""A small thread-safe event bus: the assistant publishes, the dashboard (and tests) subscribe.

Events are plain dicts with a "type" and a "ts". High-rate events ("audio",
"telemetry") are not kept in the replay history; everything else is, so a
dashboard that connects late still sees the recent transcript.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Any

TRANSIENT = {"audio", "telemetry"}


class EventBus:
    def __init__(self, history: int = 200, queue_size: int = 2000):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._queue_size = queue_size
        self.history: deque[dict[str, Any]] = deque(maxlen=history)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, type: str, **data: Any) -> dict[str, Any]:
        event = {"type": type, "ts": time.time(), **data}
        if type not in TRANSIENT:
            self.history.append(event)
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:  # a stalled subscriber never blocks the assistant
                pass
        return event

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)
