"""A conversation on the Mac's own microphone and speakers (the ElevenLabs Python SDK, our audio).

The SDK wants an AudioInterface: 16 kHz mono PCM16 in (chunks of about 250 ms), 16 kHz PCM16 out,
an interrupt() when the user talks over the agent. sounddevice provides both streams; the output
is fed from a queue by a writer thread so output() returns at once.

Without a headset a Mac hears its own speakers, and the agent would keep interrupting itself, so by
default the microphone is muted while Daxton speaks (CONVAI_BARGE_IN=false): the user waits for a
pause, as on a speakerphone. With a headset (or the browser, which has echo cancellation) barge-in
can be on.

Every skill is registered as a client tool, so the agent's tool calls run here, on the Mac. Transcript
lines go to the event bus (the dashboard log) and to ~/.daxton/conversations/ for the next session's
context. Spoken audio is mirrored to the bus as speech_chunk, so a dashboard open in a browser hears it.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

RATE = 16000
IN_CHUNK = 4000  # samples, 250 ms, what the SDK recommends
OUT_CHUNK = 1600  # 100 ms per write keeps interruptions snappy
POST_SPEECH_MUTE = 0.35  # seconds the mic stays muted after the last output chunk (room echo)


class SounddeviceAudio:
    """AudioInterface for the ElevenLabs Conversation, on sounddevice, with a half-duplex option."""

    def __init__(self, device=None, barge_in: bool = False, on_input=None, on_output=None):
        self.device = device
        self.barge_in = barge_in
        self.on_input = on_input  # callable(int16 frame) for the level meter
        self.on_output = on_output  # callable(bytes, rate) for the dashboard mirror
        self._in = None
        self._out = None
        self._q: queue.Queue = queue.Queue()
        self._writer: threading.Thread | None = None
        self._stop = threading.Event()
        self._speaking_until = 0.0
        self._interrupt = threading.Event()
        self.speaking = False

    # -- AudioInterface
    def start(self, input_callback: Callable[[bytes], None]) -> None:
        import sounddevice as sd

        self._stop.clear()

        def cb(indata, frames, time_info, status):  # noqa: ARG001
            frame = indata[:, 0].copy()
            if self.on_input:
                try:
                    self.on_input(frame)
                except Exception:
                    pass
            if not self.barge_in and (self.speaking or time.time() < self._speaking_until):
                frame = np.zeros_like(frame)  # the agent hears silence while it talks
            input_callback(frame.tobytes())

        self._in = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=IN_CHUNK, device=self.device,
                                  callback=cb)
        self._out = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16")
        self._out.start()
        self._in.start()
        self._writer = threading.Thread(target=self._pump, name="daxton-convai-out", daemon=True)
        self._writer.start()

    def stop(self) -> None:
        self._stop.set()
        self.interrupt()
        for s in (self._in, self._out):
            try:
                if s is not None:
                    s.stop()
                    s.close()
            except Exception:
                pass
        self._in = self._out = None

    def output(self, audio: bytes) -> None:
        if audio:
            self._q.put(audio)

    def interrupt(self) -> None:
        self._interrupt.set()
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    # -- playback
    def _pump(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._q.get(timeout=0.2)
            except queue.Empty:
                if self.speaking and time.time() >= self._speaking_until:
                    self.speaking = False
                continue
            self._interrupt.clear()
            self.speaking = True
            for i in range(0, len(data), OUT_CHUNK * 2):
                if self._interrupt.is_set() or self._stop.is_set():
                    break
                chunk = data[i:i + OUT_CHUNK * 2]
                if len(chunk) % 2:
                    chunk = chunk[:-1]
                try:
                    self._out.write(chunk)
                except Exception as e:
                    log.debug("output write failed: %s", e)
                    break
                if self.on_output:
                    try:
                        self.on_output(chunk, RATE)
                    except Exception:
                        pass
            self._speaking_until = time.time() + POST_SPEECH_MUTE


class LocalConversation:
    """One conversation session on the Mac, wired to the assistant (bus, skills, states)."""

    def __init__(self, assistant, agent_id: str, client_label: str = "the Mac", opening: str = ""):
        self.assistant = assistant
        self.settings = assistant.settings
        self.agent_id = agent_id
        self.client_label = client_label
        self.opening = opening
        self.transcript: list[tuple[str, str]] = []
        self.ended = threading.Event()
        self.audio: SounddeviceAudio | None = None
        self.conversation = None

    def _tools(self):
        from elevenlabs.conversational_ai.conversation import ClientTools

        tools = ClientTools()
        registry, ctx = self.assistant.registry, self.assistant.ctx
        for name in registry.names():
            def handler(params, _name=name):
                params = params if isinstance(params, dict) else {}
                result = registry.run(_name, params, ctx)
                self.assistant.bus.publish("tool", name=_name, arguments=params, result=str(result)[:500])
                return result

            tools.register(name, handler)
        return tools

    def run(self) -> list[tuple[str, str]]:
        from elevenlabs.client import ElevenLabs
        from elevenlabs.conversational_ai.conversation import Conversation, ConversationInitiationData

        from . import dynamic_variables, save_transcript

        a = self.assistant
        client = ElevenLabs(api_key=self.settings.elevenlabs_api_key)
        self.audio = SounddeviceAudio(device=None, barge_in=self.settings.convai_barge_in,
                                      on_input=lambda f: a.on_remote_frame(f, RATE),
                                      on_output=a._on_speaker_chunk)
        state_poll = threading.Event()

        def on_user(text: str) -> None:
            text = (text or "").strip()
            if text:
                self.transcript.append(("You", text))
                a.bus.publish("user", text=text)
                a._set_state("thinking")

        def on_agent(text: str) -> None:
            text = (text or "").strip()
            if text:
                self.transcript.append((self.settings.assistant_name, text))
                a.bus.publish("assistant", text=text)
                a._set_state("speaking")

        def on_correction(_original: str, corrected: str) -> None:
            if self.transcript and self.transcript[-1][0] == self.settings.assistant_name:
                self.transcript[-1] = (self.settings.assistant_name, corrected)
            a.bus.publish("notice", text="(interrupted)")

        def on_end() -> None:
            self.ended.set()

        self.conversation = Conversation(
            client, self.agent_id, requires_auth=True, audio_interface=self.audio, client_tools=self._tools(),
            config=ConversationInitiationData(dynamic_variables=dynamic_variables(self.settings, self.client_label)),
            callback_user_transcript=on_user, callback_agent_response=on_agent,
            callback_agent_response_correction=on_correction, callback_end_session=on_end,
        )
        a.bus.publish("conversation", status="start", client=self.client_label)
        a._set_state("listening")
        self.conversation.start_session()
        if self.opening:
            try:  # the words that woke Daxton are the first thing said
                time.sleep(0.8)
                self.conversation.send_user_message(self.opening)
                self.transcript.append(("You", self.opening))
                a.bus.publish("user", text=self.opening)
            except Exception as e:
                log.debug("opening message failed: %s", e)
        try:
            while not self.ended.is_set() and not a.stop_event.is_set():
                # the SDK has no "mode" callback in Python: derive listening/speaking from the output queue
                if self.audio.speaking:
                    a._set_state("speaking")
                elif a.state in ("speaking", "thinking") and not self.audio.speaking:
                    a._set_state("listening")
                state_poll.wait(0.25)
        finally:
            try:
                self.conversation.end_session()
            except Exception as e:
                log.debug("end_session: %s", e)
            try:
                self.conversation.wait_for_session_end()
            except Exception:
                pass
            a.bus.publish("speech_end", interrupted=False)
            path = save_transcript(self.settings, self.transcript, self.client_label)
            a.bus.publish("conversation", status="end", client=self.client_label, turns=len(self.transcript),
                          saved=str(path) if path else None)
            a._set_state("idle")
        return self.transcript

    def end(self) -> None:
        self.ended.set()
