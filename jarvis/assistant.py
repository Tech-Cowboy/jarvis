"""The assistant loop: wake -> listen -> transcribe -> think (tools) -> speak."""

from __future__ import annotations

import logging
import re
import threading

from .brain.base import LLM
from .brain.prompts import system_prompt
from .brain.router import Router
from .config import Settings
from .skills import SkillRegistry, load_default_skills
from .skills.context import SkillContext
from .tts import Speaker

log = logging.getLogger(__name__)


class Assistant:
    def __init__(
        self,
        settings: Settings,
        llm: LLM,
        speaker: Speaker,
        transcriber=None,
        mic=None,
        wake=None,
        registry: SkillRegistry | None = None,
    ):
        self.settings = settings
        self.llm = llm
        self.speaker = speaker
        self.transcriber = transcriber
        self.mic = mic
        self.wake = wake
        self.registry = registry or load_default_skills()
        self.stop_event = threading.Event()
        self.ctx = SkillContext(settings=settings, speak=self.speak, stop_event=self.stop_event)
        self.router = Router(
            llm, self.registry, lambda: system_prompt(settings), self.ctx,
            history_turns=settings.history_turns, max_tool_rounds=settings.max_tool_rounds,
            on_tool=self._show_tool,
        )
        self._name_re = re.compile(rf"\b{re.escape(settings.assistant_name)}\b", re.I)
        self.show_tools = True

    def _show_tool(self, name: str, arguments: dict, result: str) -> None:
        if not self.show_tools:
            return
        args = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        first = result.splitlines()[0] if result else ""
        print(f"  ↳ {name}({args}) -> {first[:90]}{'…' if len(first) > 90 else ''}", flush=True)

    # ------------------------------------------------------------------ core
    def handle(self, text: str) -> str:
        """Text in, reply text out (no audio)."""
        return self.router.ask(text)

    def speak(self, text: str) -> None:
        if not text:
            return
        try:
            self.speaker.say(text)
        except Exception as e:  # never let a TTS outage kill the loop
            log.error("speech failed: %s", e)
            print(f"{self.settings.assistant_name}: {text}")

    # ---------------------------------------------------------------- modes
    def run_chat(self, speak: bool = False) -> None:
        """Interactive text mode: type a request, read (and optionally hear) the reply."""
        name = self.settings.assistant_name
        print(f"{name} text mode. Brain: {self.llm.describe()}. Type 'exit' to quit.\n")
        while not self.stop_event.is_set():
            try:
                text = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not text:
                continue
            if text.lower() in {"exit", "quit", "q"}:
                break
            reply = self.handle(text)
            print(f"{name}: {reply}\n")
            if speak:
                self.speak(reply)

    def run_voice(self) -> None:
        """Voice mode. Requires mic, transcriber and wake detector."""
        assert self.mic and self.transcriber and self.wake, "voice mode needs mic, transcriber and wake detector"
        s = self.settings
        name = s.assistant_name
        mode = self.wake.name

        self.mic.start()
        self.mic.calibrate()
        print(f"{name} online. Brain: {self.llm.describe()}. Voice: {self.speaker.describe()}. "
              f"Ears: {self.transcriber.describe()}. Wake: {self.wake.describe()}.")
        if mode == "name":
            print(f"Say '{name}' anywhere in a sentence. Ctrl-C to quit.")
        elif mode == "wakeword":
            print("Say 'hey Jarvis' to start. Ctrl-C to quit.")
        self.speak("Online.")

        try:
            while not self.stop_event.is_set():
                if not self.wake.wait(self.stop_event):
                    break
                if mode != "name":
                    self._acknowledge()
                self.mic.flush()
                audio = self.mic.record_utterance(
                    min_speech_rms=s.min_speech_rms,
                    silence_seconds=s.silence_seconds,
                    max_seconds=s.max_utterance_seconds,
                    start_timeout=(60.0 if mode == "name" else s.listen_timeout_seconds),
                )
                if audio is None:
                    continue
                text = self.transcriber.transcribe(audio, self.mic.sample_rate)
                if not text:
                    continue
                if mode == "name":
                    if not self._name_re.search(text):
                        log.debug("ignored (no name): %s", text)
                        continue
                    text = self._strip_name(text)
                    if not text:
                        self._acknowledge()
                        continue
                print(f"You: {text}")
                reply = self.handle(text)
                print(f"{name}: {reply}")
                self.speak(reply)
        except KeyboardInterrupt:
            print("\nStopping.")
        finally:
            self.mic.stop()

    # -------------------------------------------------------------- helpers
    def _acknowledge(self) -> None:
        ack = self.settings.wake_ack
        if ack == "phrase":
            self.speak(self.settings.wake_ack_text)
        elif ack == "chime":
            try:
                from .audio.chime import play_chime
                play_chime()
            except Exception as e:
                log.debug("chime failed: %s", e)

    def _strip_name(self, text: str) -> str:
        text = self._name_re.sub("", text)
        text = re.sub(r"^\W+|\W+$", "", text.strip())
        text = re.sub(r"^(hey|ok|okay|hi|hello)\b[\s,]*", "", text, flags=re.I)
        return text.strip()
