"""macOS built-in `say` command: free, offline, zero setup. The fallback when there is no ElevenLabs key.

When something is listening for the audio (the dashboard visualiser, a browser on the
portal) the utterance is rendered to 16-bit PCM first (`say -o file.wav`) and played
through the same PCM player ElevenLabs uses, so the audio can be tapped and can stay
off the Mac's speakers. Otherwise `say` speaks directly, as it always has.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from ..audio.player import play_pcm16_bytes, playback_lock
from . import Speaker, clean_for_speech

log = logging.getLogger(__name__)

RENDER_RATE = 22050


def render_say_pcm(text: str, voice: str = "", rate: int = RENDER_RATE, timeout: float = 60.0) -> tuple[bytes, int]:
    """Render text with `say` to 16-bit mono PCM. Returns (pcm bytes, sample rate)."""
    with tempfile.TemporaryDirectory(prefix="daxton-say-") as tmp:
        path = Path(tmp) / "utterance.wav"
        cmd = ["say", "-o", str(path), f"--data-format=LEI16@{rate}"]
        if voice:
            cmd += ["-v", voice]
        subprocess.run(cmd, input=text, text=True, check=True, timeout=timeout, capture_output=True)
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                raise RuntimeError(f"unexpected say output: {w.getnchannels()} ch, {w.getsampwidth() * 8}-bit")
            return w.readframes(w.getnframes()), w.getframerate()


class SaySpeaker(Speaker):
    name = "say"

    def __init__(self, voice: str = ""):
        if platform.system() != "Darwin" or not shutil.which("say"):
            raise RuntimeError("The `say` command is only available on macOS.")
        self.voice = voice
        self._proc = None
        self._render_ok = True

    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        listening = self.on_chunk is not None and (self.pcm_wanted is None or self.pcm_wanted())
        if self._render_ok and (listening or self.silent):
            try:
                pcm, rate = render_say_pcm(text, self.voice)
                on_chunk = (lambda data: self.on_chunk(data, rate)) if self.on_chunk else None
                play_pcm16_bytes(pcm, rate, on_chunk=on_chunk, silent=self.silent)
                return
            except Exception as e:  # older macOS, odd voices, no sounddevice: fall back to speaking directly
                log.warning("say could not render to PCM (%s); speaking directly", e)
                self._render_ok = False
        if self.silent:
            return
        cmd = ["say"]
        if self.voice:
            cmd += ["-v", self.voice]
        with playback_lock:
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, text=True)
            try:
                self._proc.communicate(text)
            finally:
                self._proc = None

    def stop(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        super().stop()  # also ends a rendered utterance playing through the PCM player

    def describe(self) -> str:
        return f"macOS say ({self.voice or 'default voice'})"
