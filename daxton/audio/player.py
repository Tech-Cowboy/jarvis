"""Audio playback for streamed 16-bit PCM (what ElevenLabs sends with pcm_* output formats)."""

from __future__ import annotations

import logging
import threading
from typing import Iterable

import numpy as np

log = logging.getLogger(__name__)

# One speaker at a time: timers may speak from a background thread.
playback_lock = threading.RLock()
_stop_requested = threading.Event()


def stop_playback() -> None:
    """Ask the current PCM playback to end at the next chunk (used by the dashboard's stop button)."""
    _stop_requested.set()


def play_pcm16_stream(chunks: Iterable[bytes], sample_rate: int, on_chunk=None, silent: bool = False) -> bytes:
    """Play a stream of raw 16-bit mono PCM chunks as they arrive. Returns all bytes played (for caching).

    With silent=True nothing reaches the speakers, but the chunks are still paced in real time and
    handed to on_chunk, so a dashboard listening remotely hears the voice while the Mac stays quiet.
    """
    played = bytearray()
    leftover = b""
    with playback_lock:
        _stop_requested.clear()
        with _output(sample_rate, silent) as stream:
            for chunk in chunks:
                if _stop_requested.is_set():
                    stream.abort()
                    break
                if not chunk:
                    continue
                data = leftover + chunk
                if len(data) % 2:  # int16 needs even byte counts
                    data, leftover = data[:-1], data[-1:]
                else:
                    leftover = b""
                stream.write(data)
                played.extend(data)
                if on_chunk:
                    on_chunk(data)
            # RawOutputStream.stop() (called on exit) waits for the buffer to drain
    return bytes(played)


def play_pcm16_bytes(data: bytes, sample_rate: int, on_chunk=None, chunk_bytes: int = 4800, silent: bool = False) -> None:
    """Play a whole PCM buffer in small chunks so it stays interruptible and the analyser sees it."""
    chunks = (data[i:i + chunk_bytes] for i in range(0, len(data), chunk_bytes))
    play_pcm16_stream(chunks, sample_rate, on_chunk=on_chunk, silent=silent)


class _SilentOutput:
    """Stands in for a sounddevice output stream: sleeps for each chunk's duration instead of playing it."""

    def __init__(self, sample_rate: int):
        self.sample_rate = sample_rate

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, data: bytes) -> None:
        import time

        time.sleep(len(data) / 2 / self.sample_rate)

    def abort(self) -> None:
        pass


def _output(sample_rate: int, silent: bool):
    if silent:
        return _SilentOutput(sample_rate)
    import sounddevice as sd

    return sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16")


def play_float32(samples: np.ndarray, sample_rate: int) -> None:
    """Play a float32 numpy array in -1..1 (used for the chime)."""
    import sounddevice as sd

    with playback_lock:
        sd.play(samples.astype(np.float32), samplerate=sample_rate, blocking=True)


def pcm_rate_from_format(output_format: str, default: int = 24000) -> int:
    """'pcm_24000' -> 24000."""
    try:
        return int(output_format.split("_")[1])
    except (IndexError, ValueError):
        return default
