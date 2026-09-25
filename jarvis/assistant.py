"""The assistant loop: wake -> listen -> transcribe -> think (tools) -> speak.

Every transition is published on an EventBus so the dashboard (jarvis/ui) can
mirror it live: states, transcript lines, tool calls, routes, audio levels.
"""

from __future__ import annotations

import logging
import re
import threading
import time

from .brain.base import LLM
from .brain.prompts import system_prompt
from .brain.router import Router
from .config import Settings
from .events import EventBus
from .skills import SkillRegistry, load_default_skills
from .skills.context import SkillContext
from .tts import Speaker

log = logging.getLogger(__name__)

STATES = ("offline", "idle", "listening", "transcribing", "thinking", "speaking")

CLI_COMMANDS = {"run", "chat", "ask", "say", "listen", "rate", "doctor", "devices", "voices", "skills",
                "download-models", "ui"}


def _looks_like_cli_command(text: str) -> bool:
    """'jarvis listen', 'jarvis doctor --online', 'jarvis' typed at the chat prompt instead of the shell."""
    words = text.strip().lower().split()
    if not words or words[0] != "jarvis":
        return False
    return len(words) == 1 or words[1] in CLI_COMMANDS or words[1].startswith("-")


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
        bus: EventBus | None = None,
    ):
        self.settings = settings
        self.llm = llm
        self.speaker = speaker
        self.transcriber = transcriber
        self.mic = mic
        self.wake = wake
        self.registry = registry or load_default_skills()
        self.bus = bus or EventBus()
        self.stop_event = threading.Event()
        self.talk_event = threading.Event()  # push-to-talk from the dashboard
        self.muted = False
        self.state = "offline"
        self.started_at = time.time()
        self.ctx = SkillContext(settings=settings, speak=self.speak, stop_event=self.stop_event)
        self.router = Router(
            llm, self.registry, lambda: system_prompt(settings), self.ctx,
            history_turns=settings.history_turns, max_tool_rounds=settings.max_tool_rounds,
            on_tool=self._show_tool,
        )
        self._name_re = re.compile(rf"\b{re.escape(settings.assistant_name)}\b", re.I)
        self._turn_lock = threading.RLock()
        self.show_tools = True
        # audio hooks for the dashboard visualiser
        if self.mic is not None:
            self.mic.on_frame = self._on_mic_frame
        self.speaker.on_chunk = self._on_speaker_chunk

    # ------------------------------------------------------------------ core
    def handle(self, text: str) -> str:
        """Text in, reply text out (no audio). Thread-safe: dashboard and voice loop share the router."""
        with self._turn_lock:
            self.bus.publish("user", text=text)
            self._set_state("thinking")
            reply = self.router.ask(text)
            self.bus.publish("assistant", text=reply)
            route = getattr(self.llm, "last_route", None)
            if route is not None:  # published after the reply so the dashboard can badge the right line
                rating = route.rating
                self.bus.publish("route", tier=route.tier, llm=route.llm, escalated=route.escalated,
                                 pinned=route.pinned, score=(rating.score if rating else None),
                                 reasons=(rating.reasons if rating else []), summary=str(route))
                if self.show_tools:
                    print(f"  ↳ route: {route}", flush=True)
            return reply

    def respond(self, text: str) -> str:
        """handle() then speak the reply; the dashboard's text box and the voice loop both use this."""
        with self._turn_lock:
            reply = self.handle(text)
            self.speak(reply)
            return reply

    def speak(self, text: str) -> None:
        if not text:
            return
        self._set_state("speaking")
        try:
            self.speaker.say(text)
        except Exception as e:  # never let a TTS outage kill the loop
            log.error("speech failed: %s", e)
            self.bus.publish("error", text=f"speech failed: {e}")
            print(f"{self.settings.assistant_name}: {text}")
        finally:
            self._set_state("idle")

    def stop_speaking(self) -> None:
        try:
            self.speaker.stop()
        except Exception as e:
            log.debug("stop failed: %s", e)

    def routing_summary(self) -> str | None:
        summary = getattr(self.llm, "summary", None)
        return summary() if callable(summary) else None

    def snapshot(self) -> dict:
        """Everything a freshly connected dashboard needs."""
        from . import __version__

        s = self.settings
        tiers = s.resolved_tier_specs() if s.uses_tiers() else {}
        stats = getattr(self.llm, "stats", None)
        return {
            "type": "snapshot",
            "version": __version__,
            "assistant_name": s.assistant_name,
            "user_name": s.user_name,
            "state": self.state,
            "muted": self.muted,
            "started_at": self.started_at,
            "brain": self.llm.describe(),
            "tiers": tiers,
            "thresholds": {"fast": s.routing_fast_threshold, "smart": s.routing_smart_threshold},
            "pinned": getattr(self.llm, "pinned", None),
            "voice": self.speaker.describe(),
            "ears": self.transcriber.describe() if self.transcriber else "none (text mode)",
            "wake": self.wake.describe() if self.wake else "none (text mode)",
            "mic": (self.mic.device_spec or "system default") if self.mic else "none",
            "voice_mode": self.mic is not None,
            "skills": [{"name": t.name, "description": t.description.splitlines()[0]} for t in self.registry.tool_specs()],
            "stats": dict(stats) if stats is not None else {},
            "history": list(self.bus.history),
        }

    # ---------------------------------------------------------------- modes
    def run_chat(self, speak: bool = False) -> None:
        """Interactive text mode: type a request, read (and optionally hear) the reply."""
        name = self.settings.assistant_name
        print(f"{name} text mode. Brain: {self.llm.describe()}. Type 'exit' to quit.\n")
        self._set_state("idle")
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
            if _looks_like_cli_command(text):
                print(f"That's a terminal command, and this is {name}'s text mode. Type 'exit' first, "
                      f"then run `{text}` at the shell prompt.\n")
                continue
            reply = self.handle(text)
            print(f"{name}: {reply}\n")
            if speak:
                self.speak(reply)
        summary = self.routing_summary()
        if summary:
            print(summary)
        self._set_state("offline")

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
        if self.mic.ambient_rms == 0.0:
            print("Warning: the microphone is delivering digital silence. If nothing is heard, set MIC_DEVICE "
                  "in .env to a real input from `jarvis devices` (for example 'MacBook Pro Microphone').")
            self.bus.publish("error", text="microphone delivers digital silence; check MIC_DEVICE")
        if mode == "name":
            print(f"Say '{name}' anywhere in a sentence. Ctrl-C to quit.")
        elif mode == "wakeword":
            print("Say 'hey Jarvis' to start. Ctrl-C to quit.")
        self.bus.publish("online", mode=mode, ambient_rms=self.mic.ambient_rms)
        self.speak("Online.")
        self._set_state("idle")

        try:
            while not self.stop_event.is_set():
                if self.muted and not self.talk_event.is_set():
                    time.sleep(0.2)
                    continue
                if not self.wake.wait(self.stop_event, self.talk_event):
                    break
                self.voice_turn(mode)
        except KeyboardInterrupt:
            print("\nStopping.")
        finally:
            self._set_state("offline")
            self.mic.stop()
            summary = self.routing_summary()
            if summary:
                print(summary)

    def voice_turn(self, mode: str) -> None:
        """One listen -> transcribe -> respond cycle after the wake condition fired."""
        s = self.settings
        name = s.assistant_name
        self.bus.publish("wake", mode=mode)
        if mode != "name":
            self._acknowledge()
        self.mic.flush()
        self._set_state("listening")
        audio = self.mic.record_utterance(
            min_speech_rms=s.min_speech_rms,
            silence_seconds=s.silence_seconds,
            max_seconds=s.max_utterance_seconds,
            start_timeout=(60.0 if mode == "name" else s.listen_timeout_seconds),
        )
        if audio is None:
            self.bus.publish("heard", text="", note="timeout")
            self._set_state("idle")
            return
        self._set_state("transcribing")
        text = self.transcriber.transcribe(audio, self.mic.sample_rate)
        if not text:
            self.bus.publish("heard", text="", note="nothing intelligible")
            self._set_state("idle")
            return
        if mode == "name":
            if not self._name_re.search(text):
                log.debug("ignored (no name): %s", text)
                self._set_state("idle")
                return
            text = self._strip_name(text)
            if not text:
                self._acknowledge()
                self._set_state("idle")
                return
        print(f"You: {text}")
        reply = self.respond(text)
        print(f"{name}: {reply}")

    # -------------------------------------------------------------- helpers
    def _set_state(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        self.bus.publish("state", state=state)

    def _show_tool(self, name: str, arguments: dict, result: str) -> None:
        self.bus.publish("tool", name=name, arguments=arguments, result=result[:500])
        if not self.show_tools:
            return
        args = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        first = result.splitlines()[0] if result else ""
        print(f"  ↳ {name}({args}) -> {first[:90]}{'…' if len(first) > 90 else ''}", flush=True)

    def _on_mic_frame(self, frame) -> None:
        if self.bus.subscribers == 0:
            return
        from .audio.analysis import analyze

        rms, bands = analyze(frame, self.mic.sample_rate)
        self.bus.publish("audio", source="mic", rms=round(rms, 4), bands=[round(b, 3) for b in bands])

    def _on_speaker_chunk(self, data: bytes, sample_rate: int) -> None:
        if self.bus.subscribers == 0:
            return
        from .audio.analysis import analyze, pcm16_bytes_to_samples

        rms, bands = analyze(pcm16_bytes_to_samples(data), sample_rate)
        self.bus.publish("audio", source="speaker", rms=round(rms, 4), bands=[round(b, 3) for b in bands])

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
