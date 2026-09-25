"""Voice over the dashboard link: the browser's microphone in, the assistant's voice out.

In:  the page streams 16-bit mono PCM frames (binary WebSocket messages) after a
     {"cmd": "voice_start"}; VoiceCapture runs the same energy VAD the Mac's
     microphone uses and decides when the utterance is over.
Out: every PCM chunk the speaker plays (an ElevenLabs stream, or the macOS voice
     rendered to PCM) is published on the event bus as "speech_chunk", in order
     with the transcript events; SpeechRelay turns that into binary frames for a
     page that asked for audio, bracketed by {"type": "speech_start"} and
     {"type": "speech_end"}.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

MAX_FRAME_BYTES = 64_000  # 2 s of 16 kHz PCM; anything bigger is not a microphone frame


class VoiceCapture:
    """Accumulates PCM16 frames from one browser and ends the utterance on silence, length or timeout."""

    def __init__(self, sample_rate: int = 16000, min_speech_rms: float = 0.010, silence_seconds: float = 1.2,
                 max_seconds: float = 15.0, start_timeout: float = 8.0):
        self.sample_rate = int(sample_rate)
        self.min_speech_rms = min_speech_rms
        self.silence_seconds = silence_seconds
        self.max_seconds = max_seconds
        self.start_timeout = start_timeout
        self.frames: list[np.ndarray] = []
        self.speech_started = False
        self.silence = 0.0
        self.total = 0.0
        self.peak = 0.0

    def feed(self, data: bytes) -> tuple[str | None, float]:
        """Add one frame. Returns (reason the utterance ended or None, this frame's rms)."""
        usable = len(data) - (len(data) % 2)
        frame = np.frombuffer(data[:usable], dtype="<i2")
        if frame.size == 0:
            return None, 0.0
        self.frames.append(frame)
        seconds = frame.size / self.sample_rate
        self.total += seconds
        x = frame.astype(np.float32) / 32768.0
        level = float(np.sqrt(np.mean(x * x)))
        self.peak = max(self.peak, level)
        if level >= self.min_speech_rms:
            self.speech_started = True
            self.silence = 0.0
        elif self.speech_started:
            self.silence += seconds
            if self.silence >= self.silence_seconds:
                return "silence", level
        if self.total >= self.max_seconds:
            return "max_length", level
        if not self.speech_started and self.total >= self.start_timeout:
            return "timeout", level
        return None, level

    def audio(self) -> np.ndarray | None:
        """float32 mono in -1..1, or None when no speech was heard."""
        if not self.speech_started or not self.frames:
            return None
        pcm = np.concatenate(self.frames)
        return pcm.astype(np.float32) / 32768.0

    @property
    def seconds(self) -> float:
        return self.total


class SpeechRelay:
    """Turns "speech_chunk" / "speech_end" bus events into what one page receives: JSON markers and PCM frames."""

    def __init__(self, put: Callable[[Any], None]):
        self.put = put
        self.enabled = False
        self._rate: int | None = None
        self._id = 0

    def handle(self, event: dict[str, Any]) -> bool:
        """Consume a speech event. Returns True when the event was one (and must not be forwarded as JSON)."""
        kind = event.get("type")
        if kind == "speech_chunk":
            if self.enabled and event.get("data"):
                rate = int(event.get("rate", 0))
                if self._rate != rate:
                    self.end(interrupted=False)
                    self._id += 1
                    self._rate = rate
                    self.put({"type": "speech_start", "id": self._id, "rate": rate})
                self.put(bytes(event["data"]))
            return True
        if kind == "speech_end":
            self.end(interrupted=bool(event.get("interrupted", False)))
            return True
        return False

    def end(self, interrupted: bool) -> None:
        if self._rate is not None:
            self._rate = None
            self.put({"type": "speech_end", "id": self._id, "interrupted": interrupted})
