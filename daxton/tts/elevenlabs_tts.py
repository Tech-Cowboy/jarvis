"""ElevenLabs voice: streamed raw PCM played as it arrives (no ffmpeg needed).

Short phrases are cached on disk (~/.daxton/tts-cache) so the wake acknowledgement
and repeated confirmations do not spend credits twice.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from ..audio.player import pcm_rate_from_format, play_pcm16_bytes, play_pcm16_stream
from ..config import Settings
from . import Speaker, clean_for_speech

log = logging.getLogger(__name__)

CACHE_MAX_CHARS = 80


class ElevenLabsSpeaker(Speaker):
    name = "elevenlabs"

    def __init__(self, settings: Settings):
        from elevenlabs.client import ElevenLabs
        from elevenlabs.types import VoiceSettings

        if not settings.elevenlabs_api_key:
            raise ValueError("ELEVENLABS_API_KEY is not set.")
        self.client = ElevenLabs(api_key=settings.elevenlabs_api_key)
        self.model_id = settings.elevenlabs_model
        self.output_format = settings.elevenlabs_output_format
        if not self.output_format.startswith("pcm_"):
            log.warning("ELEVENLABS_OUTPUT_FORMAT must be a pcm_* format for built-in playback; using pcm_24000")
            self.output_format = "pcm_24000"
        self.sample_rate = pcm_rate_from_format(self.output_format)
        self.voice_settings = VoiceSettings(
            stability=settings.elevenlabs_stability,
            similarity_boost=settings.elevenlabs_similarity,
            style=settings.elevenlabs_style,
            use_speaker_boost=True,
            speed=settings.elevenlabs_speed,
        )
        self.voice_id = settings.elevenlabs_voice_id
        if settings.elevenlabs_voice_name:
            self.voice_id = self.resolve_voice(settings.elevenlabs_voice_name) or self.voice_id
        self.cache_dir: Path | None = (settings.data_dir / "tts-cache") if settings.tts_cache else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- voices
    def list_voices(self) -> list[tuple[str, str, str]]:
        """(voice_id, name, category) for every voice on the account, stock voices included."""
        resp = self.client.voices.get_all()
        return [(v.voice_id, v.name or "", getattr(v, "category", "") or "") for v in resp.voices]

    def resolve_voice(self, name: str) -> str | None:
        wanted = name.strip().lower()
        for voice_id, vname, _cat in self.list_voices():
            if vname.strip().lower() == wanted:
                return voice_id
        for voice_id, vname, _cat in self.list_voices():
            if wanted in vname.lower():
                return voice_id
        log.warning("no ElevenLabs voice named '%s'; using voice id %s", name, self.voice_id)
        return None

    # ------------------------------------------------------------------ speak
    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        cache_path = self._cache_path(text)
        on_chunk = (lambda data: self.on_chunk(data, self.sample_rate)) if self.on_chunk else None
        if cache_path and cache_path.is_file():
            play_pcm16_bytes(cache_path.read_bytes(), self.sample_rate, on_chunk=on_chunk)
            return
        stream = self.client.text_to_speech.stream(
            voice_id=self.voice_id,
            text=text,
            model_id=self.model_id,
            output_format=self.output_format,
            voice_settings=self.voice_settings,
        )
        played = play_pcm16_stream(stream, self.sample_rate, on_chunk=on_chunk)
        if cache_path and played:
            try:
                cache_path.write_bytes(played)
            except OSError:
                pass

    def synthesize(self, text: str) -> bytes:
        """Return raw PCM without playing it (for tests and exports)."""
        return b"".join(
            self.client.text_to_speech.convert(
                voice_id=self.voice_id, text=clean_for_speech(text), model_id=self.model_id,
                output_format=self.output_format, voice_settings=self.voice_settings,
            )
        )

    def _cache_path(self, text: str) -> Path | None:
        if not self.cache_dir or len(text) > CACHE_MAX_CHARS:
            return None
        key = hashlib.sha1(f"{self.voice_id}|{self.model_id}|{self.output_format}|{text}".encode()).hexdigest()
        return self.cache_dir / f"{key}.pcm"

    def describe(self) -> str:
        return f"ElevenLabs {self.model_id} voice {self.voice_id}"
