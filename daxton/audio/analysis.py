"""Cheap audio analysis for the dashboard: an RMS level and a 16-band log-spaced spectrum per frame."""

from __future__ import annotations

import numpy as np

N_BANDS = 16
_windows: dict[int, np.ndarray] = {}
_edges: dict[tuple[int, int], np.ndarray] = {}


def _window(n: int) -> np.ndarray:
    w = _windows.get(n)
    if w is None:
        w = np.hanning(n).astype(np.float32)
        _windows[n] = w
    return w


def _band_edges(n: int, sample_rate: int) -> np.ndarray:
    key = (n, sample_rate)
    e = _edges.get(key)
    if e is None:
        top = min(8000.0, sample_rate / 2.0)
        e = np.geomspace(80.0, top, N_BANDS + 1)
        _edges[key] = e
    return e


def analyze(samples: np.ndarray, sample_rate: int) -> tuple[float, list[float]]:
    """(rms 0..1, bands 0..1) for int16 or float32 mono samples. Returns silence for empty input."""
    if samples.size == 0:
        return 0.0, [0.0] * N_BANDS
    x = samples.astype(np.float32)
    if samples.dtype != np.float32:
        x /= 32768.0
    rms = float(np.sqrt(np.mean(x * x)))
    n = x.size
    spec = np.abs(np.fft.rfft(x * _window(n)))
    freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
    edges = _band_edges(n, sample_rate)
    bands: list[float] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (freqs >= lo) & (freqs < hi)
        mag = float(spec[mask].mean()) if mask.any() else 0.0
        db = 20.0 * np.log10(mag / n * 2.0 + 1e-9)  # roughly -100..0 dBFS
        bands.append(float(min(1.0, max(0.0, (db + 70.0) / 70.0))))
    return rms, bands


def pcm16_bytes_to_samples(data: bytes) -> np.ndarray:
    usable = len(data) - (len(data) % 2)
    return np.frombuffer(data[:usable], dtype="<i2")
