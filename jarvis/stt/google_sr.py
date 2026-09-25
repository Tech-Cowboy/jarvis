"""Google Web Speech via the SpeechRecognition library: free, online, no model download.

This is the recognizer every classic Python JARVIS tutorial used. It needs an
internet connection and is less accurate than Whisper, but it works instantly.
"""

from __future__ import annotations

import logging

import numpy as np

from . import Transcriber, clean_transcript

log = logging.getLogger(__name__)


class GoogleTranscriber(Transcriber):
    name = "google"

    def __init__(self, language: str = "en"):
        import speech_recognition as sr

        self._sr = sr
        self.recognizer = sr.Recognizer()
        self.language = {"en": "en-US"}.get(language, language or "en-US")

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        data = self._sr.AudioData(pcm, sample_rate, 2)
        try:
            text = self.recognizer.recognize_google(data, language=self.language)
        except self._sr.UnknownValueError:
            return ""
        except self._sr.RequestError as e:
            log.error("Google speech request failed: %s", e)
            return ""
        return clean_transcript(text)

    def describe(self) -> str:
        return "Google Web Speech (online)"
