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
        self._proc = None

    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
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

    def describe(self) -> str:
        return f"macOS say ({self.voice or 'default voice'})"
