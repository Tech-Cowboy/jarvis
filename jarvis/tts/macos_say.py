"""macOS built-in `say` command: free, offline, zero setup. The fallback when there is no ElevenLabs key."""

from __future__ import annotations

import platform
import shutil
import subprocess

from ..audio.player import playback_lock
from . import Speaker, clean_for_speech


class SaySpeaker(Speaker):
    name = "say"

    def __init__(self, voice: str = ""):
        if platform.system() != "Darwin" or not shutil.which("say"):
            raise RuntimeError("The `say` command is only available on macOS.")
        self.voice = voice

    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        cmd = ["say"]
        if self.voice:
            cmd += ["-v", self.voice]
        with playback_lock:
            subprocess.run(cmd, input=text, text=True, check=False)

    def describe(self) -> str:
        return f"macOS say ({self.voice or 'default voice'})"
