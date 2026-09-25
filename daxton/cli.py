"""Command line entry point: `daxton` (voice), `daxton chat`, `daxton doctor`, ..."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__
from .config import Settings, load_settings


def _setup_logging(verbose: bool, settings: Settings) -> None:
    level = logging.DEBUG if verbose else getattr(logging, settings.log_level, logging.WARNING)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "anthropic", "faster_whisper", "ctranslate2",
                  "primp", "ddgs", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.ERROR if not verbose else logging.INFO)


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> None:
    for attr, key in (("llm", "llm_provider"), ("tts", "tts_provider"), ("stt", "stt_provider"), ("wake", "wake_mode"),
                      ("tier", "llm_routing")):
        val = getattr(args, attr, None)
        if val:
            setattr(settings, key, val)


def _build_assistant(settings: Settings, voice: bool):
    from .assistant import Assistant
    from .brain.factory import make_brain
    from .tts import make_speaker

    llm = make_brain(settings)
    speaker = make_speaker(settings)
    if not voice:
        return Assistant(settings, llm, speaker)

    from .audio.mic import Microphone
    from .stt import make_transcriber

    mic = Microphone(sample_rate=settings.sample_rate, device=settings.mic_device)
    transcriber = make_transcriber(settings)
    mode = settings.resolved_wake_mode()
    if mode == "wakeword":
        from .wake.oww import OpenWakeWordDetector
        wake = OpenWakeWordDetector(mic, settings.resolved_wake_word(), settings.wake_threshold)
    elif mode == "push_to_talk":
        from .wake.simple import PushToTalk
        wake = PushToTalk()
    elif mode == "name":
        from .wake.simple import AlwaysListening
        wake = AlwaysListening()
    else:
        raise SystemExit(f"Unknown WAKE_MODE '{mode}'. Use auto, wakeword, name or push_to_talk.")
    return Assistant(settings, llm, speaker, transcriber=transcriber, mic=mic, wake=wake)


# ------------------------------------------------------------------ commands
def cmd_run(settings: Settings, args: argparse.Namespace) -> int:
    assistant = _build_assistant(settings, voice=True)
    assistant.run_voice()
    return 0


def cmd_chat(settings: Settings, args: argparse.Namespace) -> int:
    if not args.speak and not args.tts:
        settings.tts_provider = "console"
    assistant = _build_assistant(settings, voice=False)
    assistant.run_chat(speak=args.speak)
    return 0


def cmd_ask(settings: Settings, args: argparse.Namespace) -> int:
    if not args.speak and not args.tts:
        settings.tts_provider = "console"
    assistant = _build_assistant(settings, voice=False)
    reply = assistant.handle(" ".join(args.text))
    print(reply)
    if args.speak:
        assistant.speak(reply)
    return 0


def cmd_say(settings: Settings, args: argparse.Namespace) -> int:
    from .tts import make_speaker

    speaker = make_speaker(settings)
    print(f"Speaking with {speaker.describe()} ...")
    speaker.say(" ".join(args.text))
    return 0


def cmd_listen(settings: Settings, args: argparse.Namespace) -> int:
    from .audio.mic import Microphone
    from .stt import make_transcriber

    transcriber = make_transcriber(settings)
    with Microphone(sample_rate=settings.sample_rate, device=settings.mic_device) as mic:
        mic.calibrate()
        print(f"Ambient noise rms={mic.ambient_rms:.4f}. Speak now ...")
        if mic.ambient_rms == 0.0:
            print("(digital silence: if nothing is heard, set MIC_DEVICE to a real input from `daxton devices`)")
        audio = mic.record_utterance(
            min_speech_rms=settings.min_speech_rms, silence_seconds=settings.silence_seconds,
            max_seconds=settings.max_utterance_seconds, start_timeout=settings.listen_timeout_seconds,
        )
    if audio is None:
        print("Heard nothing (timeout). Try MIN_SPEECH_RMS lower, or check the mic with `daxton devices`.")
        return 1
    print(f"Recorded {len(audio) / settings.sample_rate:.1f}s, transcribing with {transcriber.describe()} ...")
    print("Heard:", transcriber.transcribe(audio, settings.sample_rate) or "(nothing intelligible)")
    return 0


def cmd_rate(settings: Settings, args: argparse.Namespace) -> int:
    """Show how the complexity rater scores a request and which tier would take it."""
    from .brain.factory import parse_spec
    from .brain.rating import ComplexityRater
    from .skills import load_default_skills

    specs = settings.resolved_tier_specs()
    rater = ComplexityRater(settings.routing_fast_threshold, settings.routing_smart_threshold,
                            free_is_llm=(parse_spec(specs["free"])[0] != "keyword"),
                            assistant_name=settings.assistant_name)
    tools = set(load_default_skills().names())
    texts = [" ".join(args.text)] if args.text else [line.strip() for line in sys.stdin if line.strip()]
    for text in texts:
        r = rater.rate(text, tools)
        print(f'"{text}"\n  score {r.score:.2f}  ->  {r.tier} tier  ({specs[r.tier]})')
        print("  " + "; ".join(r.reasons))
    print(f"\nthresholds: fast >= {settings.routing_fast_threshold}, smart >= {settings.routing_smart_threshold}"
          f"   tiers: free={specs['free']}  fast={specs['fast']}  smart={specs['smart']}")
    return 0


def cmd_ui(settings: Settings, args: argparse.Namespace) -> int:
    """Start the dashboard server, open it, and run the assistant (voice loop unless --no-voice)."""
    import time as _time

    from .ui.server import open_dashboard, run_server

    voice = not args.no_voice
    assistant = None
    if voice:
        try:
            assistant = _build_assistant(settings, voice=True)
        except Exception as e:
            print(f"voice mode unavailable ({e}); starting the dashboard in text mode", file=sys.stderr)
            voice = False
    if assistant is None:
        settings.tts_provider = settings.tts_provider if args.tts else settings.resolved_tts_provider()
        assistant = _build_assistant(settings, voice=False)
    url = f"http://{args.host}:{args.port}"
    run_server(assistant, host=args.host, port=args.port)
    print(f"Dashboard: {url}")
    if not args.no_browser:
        print(f"Opened in {open_dashboard(url, app_window=args.app)}.")
    if voice:
        assistant.run_voice()
        return 0
    assistant._set_state("idle")
    print("Text mode: type requests in the dashboard. Ctrl-C to stop.")
    try:
        while not assistant.stop_event.is_set():
            _time.sleep(0.25)
    except KeyboardInterrupt:
        print()
    return 0


def cmd_doctor(settings: Settings, args: argparse.Namespace) -> int:
    from .doctor import FAIL, format_checks, run_checks

    checks = run_checks(settings, online=args.online)
    print(format_checks(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0


def cmd_devices(settings: Settings, args: argparse.Namespace) -> int:
    from .audio.mic import list_input_devices

    for d in list_input_devices():
        print(f"[{d['index']:2d}] {d['name']}  ({d['channels']} ch, {d['default_samplerate']:.0f} Hz)")
    print("\nSet MIC_DEVICE in .env to an index or a name substring.")
    return 0


def cmd_voices(settings: Settings, args: argparse.Namespace) -> int:
    from .tts.elevenlabs_tts import ElevenLabsSpeaker

    sp = ElevenLabsSpeaker(settings)
    for voice_id, name, category in sp.list_voices():
        marker = " <- current" if voice_id == sp.voice_id else ""
        print(f"{voice_id}  {name:24s} {category}{marker}")
    print("\nSet ELEVENLABS_VOICE_ID (or ELEVENLABS_VOICE_NAME) in .env.")
    return 0


def cmd_skills(settings: Settings, args: argparse.Namespace) -> int:
    from .skills import load_default_skills

    reg = load_default_skills()
    for spec in sorted(reg.tool_specs(), key=lambda t: t.name):
        params = ", ".join(spec.parameters.get("properties", {}))
        print(f"{spec.name}({params})\n    {spec.description.splitlines()[0]}")
    return 0


def cmd_download(settings: Settings, args: argparse.Namespace) -> int:
    if settings.resolved_stt_provider() == "whisper":
        print(f"Downloading faster-whisper {settings.whisper_model} ...")
        from .stt.whisper_local import WhisperTranscriber
        WhisperTranscriber(settings.whisper_model, settings.whisper_device, settings.whisper_compute_type)
        print("  done.")
    word = settings.resolved_wake_word()
    if not word:
        print(f"No wake-word model is trained for '{settings.assistant_name}': voice mode listens for the name in each "
              f"sentence instead. To use a wake phrase, train one with openWakeWord and set WAKE_WORD=/path/to/model.onnx.")
        return 0
    try:
        from .wake.oww import download_models, model_files_present
        if model_files_present(word):
            print(f"openWakeWord '{word}' already present.")
        else:
            print(f"Downloading openWakeWord '{word}' ...")
            download_models(word)
            print("  done.")
    except ImportError:
        print("openWakeWord not installed; skipping wake word model (pip install openwakeword onnxruntime).")
    return 0


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="daxton", description="Daxton AI: a classic Python voice assistant, modernized.")
    p.add_argument("--version", action="version", version=f"daxton {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--llm", choices=["auto", "anthropic", "openai", "ollama", "keyword"],
                   help="pin one provider (turns tiered routing off)")
    p.add_argument("--tier", choices=["auto", "free", "fast", "smart", "off"],
                   help="override LLM_ROUTING: auto rates each request; free/fast/smart pins a tier")
    p.add_argument("--tts", choices=["auto", "elevenlabs", "say", "console"], help="override TTS_PROVIDER")
    p.add_argument("--stt", choices=["auto", "whisper", "google"], help="override STT_PROVIDER")
    p.add_argument("--wake", choices=["auto", "wakeword", "name", "push_to_talk"], help="override WAKE_MODE")
    sub = p.add_subparsers(dest="command")

    sub.add_parser("run", help="voice mode (default)").set_defaults(func=cmd_run)
    c = sub.add_parser("chat", help="text mode: type requests, read replies")
    c.add_argument("--speak", action="store_true", help="also speak replies aloud")
    c.set_defaults(func=cmd_chat)
    a = sub.add_parser("ask", help="one-shot text request")
    a.add_argument("text", nargs="+")
    a.add_argument("--speak", action="store_true")
    a.set_defaults(func=cmd_ask)
    s = sub.add_parser("say", help="speak text with the configured voice")
    s.add_argument("text", nargs="+")
    s.set_defaults(func=cmd_say)
    sub.add_parser("listen", help="record one utterance and print the transcript").set_defaults(func=cmd_listen)
    u = sub.add_parser("ui", help="open the dashboard (voice mode plus a live web HUD)")
    u.add_argument("--host", default="127.0.0.1")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-voice", action="store_true", help="dashboard only, no microphone loop")
    u.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    u.add_argument("--app", action="store_true", help="open as a chromeless app window (Chrome, Brave, Edge)")
    u.set_defaults(func=cmd_ui)
    r = sub.add_parser("rate", help="show the complexity score and tier a request would get")
    r.add_argument("text", nargs="*", help="the request (or pipe lines on stdin)")
    r.set_defaults(func=cmd_rate)
    d = sub.add_parser("doctor", help="check the setup")
    d.add_argument("--online", action="store_true", help="also call the LLM and ElevenLabs APIs")
    d.set_defaults(func=cmd_doctor)
    sub.add_parser("devices", help="list microphones").set_defaults(func=cmd_devices)
    sub.add_parser("voices", help="list ElevenLabs voices").set_defaults(func=cmd_voices)
    sub.add_parser("skills", help="list the tools the brain can call").set_defaults(func=cmd_skills)
    sub.add_parser("download-models", help="pre-download Whisper and wake word models").set_defaults(func=cmd_download)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    _setup_logging(args.verbose, settings)
    _apply_overrides(settings, args)
    func = getattr(args, "func", cmd_run)
    try:
        return func(settings, args)
    except KeyboardInterrupt:
        print()
        return 130
    except Exception as e:
        if args.verbose:
            raise
        print(f"error: {e}", file=sys.stderr)
        print("run with -v for the traceback, or `daxton doctor` to check the setup", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
