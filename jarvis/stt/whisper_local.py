"""Local speech-to-text with faster-whisper (CTranslate2). Free, offline, runs well on Apple Silicon."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from . import Transcriber, clean_transcript

log = logging.getLogger(__name__)


def model_is_cached(model_name: str) -> bool:
    """True if the faster-whisper weights are already in the Hugging Face cache."""
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    return any(cache.glob(f"models--*faster-whisper-{model_name}*")) if cache.is_dir() else False


class WhisperTranscriber(Transcriber):
    name = "whisper"

    def __init__(self, model: str = "base.en", device: str = "cpu", compute_type: str = "int8", language: str = "en"):
        from faster_whisper import WhisperModel

        log.info("loading faster-whisper %s (%s, %s)", model, device, compute_type)
        self.model_name = model
        self.language = language or None
        self.model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if sample_rate != 16000:
            audio = _resample(audio, sample_rate, 16000)
        segments, _info = self.model.transcribe(
            audio.astype(np.float32),
            language=self.language,
            beam_size=1,
            vad_filter=True,
            without_timestamps=True,
            condition_on_previous_text=False,
        )
        text = " ".join(s.text.strip() for s in segments)
        return clean_transcript(text)

    def describe(self) -> str:
        return f"faster-whisper {self.model_name}"


def _resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    n = int(len(audio) * dst / src)
    x_old = np.linspace(0, 1, len(audio), endpoint=False)
    x_new = np.linspace(0, 1, n, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)
