"""Text-to-speech: ElevenLabs (the voice), macOS `say` (fallback), or console (no audio)."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from ..config import Settings


class Speaker(ABC):
    name: str = "tts"
    on_chunk = None  # optional callable(pcm16_bytes, sample_rate) fed while audio plays (dashboard visualiser)

    @abstractmethod
    def say(self, text: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        """Interrupt the current utterance if the backend supports it."""
        from ..audio.player import stop_playback

        stop_playback()

    def describe(self) -> str:
        return self.name


def clean_for_speech(text: str) -> str:
    """Strip markdown and URLs so the voice does not read symbols aloud."""
    t = text
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"https?://([^\s/]+)[^\s]*", lambda m: m.group(1).replace("www.", ""), t)
    t = re.sub(r"[*_#>]+", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    t = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = t.replace("\n", " ")
    return t.strip()


def make_speaker(settings: Settings, provider: str | None = None) -> Speaker:
    provider = (provider or settings.resolved_tts_provider()).lower()
    if provider == "elevenlabs":
        from .elevenlabs_tts import ElevenLabsSpeaker
        return ElevenLabsSpeaker(settings)
    if provider == "say":
        from .macos_say import SaySpeaker
        return SaySpeaker(settings.say_voice)
    if provider == "console":
        from .console import ConsoleSpeaker
        return ConsoleSpeaker(settings.assistant_name)
    raise ValueError(f"Unknown TTS_PROVIDER '{provider}'. Use auto, elevenlabs, say or console.")
