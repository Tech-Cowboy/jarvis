"""A short two-tone chime, generated in numpy so no audio files ship with the repo."""

from __future__ import annotations

import numpy as np

from .player import play_float32


def chime_samples(sample_rate: int = 24000) -> np.ndarray:
    def tone(freq: float, seconds: float, volume: float = 0.25) -> np.ndarray:
        t = np.linspace(0, seconds, int(sample_rate * seconds), endpoint=False)
        env = np.minimum(1.0, np.minimum(t / 0.01, (seconds - t) / 0.03))  # quick fade in/out
        return (volume * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    gap = np.zeros(int(sample_rate * 0.03), dtype=np.float32)
    return np.concatenate([tone(880.0, 0.11), gap, tone(1318.5, 0.14)])


def play_chime(sample_rate: int = 24000) -> None:
    play_float32(chime_samples(sample_rate), sample_rate)
