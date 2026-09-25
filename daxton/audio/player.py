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


def play_pcm16_stream(chunks: Iterable[bytes], sample_rate: int, on_chunk=None) -> bytes:
    """Play a stream of raw 16-bit mono PCM chunks as they arrive. Returns all bytes played (for caching)."""
    import sounddevice as sd

    played = bytearray()
    leftover = b""
    with playback_lock:
        _stop_requested.clear()
        with sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16") as stream:
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


def play_pcm16_bytes(data: bytes, sample_rate: int, on_chunk=None, chunk_bytes: int = 4800) -> None:
    """Play a whole PCM buffer in small chunks so it stays interruptible and the analyser sees it."""
    chunks = (data[i:i + chunk_bytes] for i in range(0, len(data), chunk_bytes))
    play_pcm16_stream(chunks, sample_rate, on_chunk=on_chunk)


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
