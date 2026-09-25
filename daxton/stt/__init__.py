"""Speech-to-text: local faster-whisper (default) or Google Web Speech (SpeechRecognition)."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from ..config import Settings

# Whisper is known to hallucinate these on silence / noise.
HALLUCINATIONS = {"you", "thank you.", "thanks for watching.", "thank you for watching.", "bye.", ".", "", "uh", "um"}


class Transcriber(ABC):
    name: str = "stt"

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """float32 mono audio in -1..1 -> text ('' if nothing intelligible)."""
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


def clean_transcript(text: str) -> str:
    text = " ".join(text.split()).strip()
    if text.lower() in HALLUCINATIONS:
        return ""
    return text


def make_transcriber(settings: Settings, provider: str | None = None) -> Transcriber:
    provider = (provider or settings.resolved_stt_provider()).lower()
    if provider == "whisper":
        from .whisper_local import WhisperTranscriber
        return WhisperTranscriber(settings.whisper_model, settings.whisper_device, settings.whisper_compute_type,
                                  settings.whisper_language)
    if provider == "google":
        from .google_sr import GoogleTranscriber
        return GoogleTranscriber(settings.whisper_language)
    raise ValueError(f"Unknown STT_PROVIDER '{provider}'. Use auto, whisper or google.")
