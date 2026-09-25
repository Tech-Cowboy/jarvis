"""Microphone capture with a simple energy-based voice activity detector.

Frames are 16-bit mono PCM at 16 kHz in 80 ms chunks (1280 samples), which is
exactly what openWakeWord wants and trivially resampled for Whisper (which
takes float32 at 16 kHz).
"""

from __future__ import annotations

import logging
import queue
import time
from typing import Iterator

import numpy as np

log = logging.getLogger(__name__)


def rms(frame: np.ndarray) -> float:
    """Root-mean-square level of an int16 frame, normalised to 0..1."""
    if frame.size == 0:
        return 0.0
    x = frame.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(x * x)))


def list_input_devices() -> list[dict]:
    import sounddevice as sd

    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            out.append({"index": i, "name": d["name"], "channels": d["max_input_channels"],
                        "default_samplerate": d.get("default_samplerate")})
    return out


def resolve_device(spec: str):
    """MIC_DEVICE may be an index, a name substring, or empty for the system default."""
    if not spec:
        return None
    if spec.isdigit():
        return int(spec)
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0 and spec.lower() in d["name"].lower():
            return i
    raise ValueError(f"No input device matches '{spec}'. Run `jarvis devices` to list them.")


class Microphone:
    def __init__(self, sample_rate: int = 16000, frame_ms: int = 80, device: str = ""):
        self.sample_rate = sample_rate
        self.frame_samples = int(sample_rate * frame_ms / 1000)
        self.device_spec = device
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=400)  # ~32 s of audio
        self._stream = None
        self.ambient_rms = 0.0

    # ----------------------------------------------------------------- stream
    def start(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        device = resolve_device(self.device_spec)

        def callback(indata, frames, time_info, status):  # noqa: ARG001
            if status:
                log.debug("mic status: %s", status)
            try:
                self._queue.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass  # drop audio rather than block the audio thread

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_samples,
            device=device,
            callback=callback,
        )
        self._stream.start()
        log.info("microphone started (%s Hz, device=%s)", self.sample_rate, device if device is not None else "default")

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "Microphone":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ----------------------------------------------------------------- frames
    def read_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def frames(self) -> Iterator[np.ndarray]:
        while True:
            f = self.read_frame()
            if f is not None:
                yield f

    def flush(self) -> None:
        """Discard buffered audio (e.g. what the mic heard while we were speaking)."""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # -------------------------------------------------------------- utterance
    def calibrate(self, seconds: float = 0.6) -> float:
        """Measure ambient noise so thresholds adapt to the room."""
        self.flush()
        levels = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            f = self.read_frame(timeout=0.5)
            if f is not None:
                levels.append(rms(f))
        self.ambient_rms = float(np.median(levels)) if levels else 0.0
        log.info("ambient noise rms=%.4f", self.ambient_rms)
        return self.ambient_rms

    def record_utterance(
        self,
        min_speech_rms: float = 0.010,
        silence_seconds: float = 1.2,
        max_seconds: float = 15.0,
        start_timeout: float = 8.0,
        pre_roll_frames: int = 4,
    ) -> np.ndarray | None:
        """Wait for speech, record until `silence_seconds` of quiet, return float32 audio (or None on timeout)."""
        speech_threshold = max(min_speech_rms, self.ambient_rms * 3.0)
        silence_threshold = max(min_speech_rms * 0.6, self.ambient_rms * 1.8)
        frame_seconds = self.frame_samples / self.sample_rate

        pre_roll: list[np.ndarray] = []
        recorded: list[np.ndarray] = []
        started = False
        quiet_frames = 0
        deadline = time.time() + start_timeout
        max_frames = int(max_seconds / frame_seconds)
        needed_quiet = max(1, int(silence_seconds / frame_seconds))

        while True:
            f = self.read_frame(timeout=0.5)
            if f is None:
                if not started and time.time() > deadline:
                    return None
                continue
            level = rms(f)
            if not started:
                pre_roll.append(f)
                if len(pre_roll) > pre_roll_frames:
                    pre_roll.pop(0)
                if level >= speech_threshold:
                    started = True
                    recorded.extend(pre_roll)
                elif time.time() > deadline:
                    return None
                continue
            recorded.append(f)
            if level < silence_threshold:
                quiet_frames += 1
                if quiet_frames >= needed_quiet:
                    break
            else:
                quiet_frames = 0
            if len(recorded) >= max_frames:
                break

        audio = np.concatenate(recorded).astype(np.float32) / 32768.0
        log.info("utterance: %.1fs", len(audio) / self.sample_rate)
        return audio
