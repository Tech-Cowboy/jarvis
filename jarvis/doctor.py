"""`jarvis doctor`: check every part of the pipeline and say what to fix."""

from __future__ import annotations

import importlib
import platform
import shutil
import sys
from dataclasses import dataclass

from .config import Settings

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


@dataclass
class Check:
    status: str
    area: str
    detail: str
    hint: str = ""


def _importable(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


def run_checks(settings: Settings, online: bool = False) -> list[Check]:
    checks: list[Check] = []
    add = checks.append

    # --- environment
    add(Check(OK, "python", f"{platform.python_version()} on {platform.system()} {platform.machine()}"))
    if sys.version_info < (3, 11):
        add(Check(FAIL, "python", "Python 3.11+ is required"))
    files = ", ".join(str(p) for p in settings.env_files) or "none found"
    add(Check(OK if settings.env_files else WARN, "config", f".env files loaded: {files}",
              "" if settings.env_files else "copy .env.example to .env and add your keys"))

    # --- brain
    provider = settings.resolved_llm_provider()
    if provider == "anthropic":
        ok = _importable("anthropic")
        add(Check(OK if ok else FAIL, "brain", f"Anthropic, model {settings.anthropic_model}, key {settings.masked(settings.anthropic_api_key)}",
                  "" if ok else "pip install anthropic"))
    elif provider == "openai":
        ok = _importable("openai")
        add(Check(OK if ok else FAIL, "brain", f"OpenAI, model {settings.openai_model}, key {settings.masked(settings.openai_api_key)}",
                  "" if ok else "pip install openai"))
    elif provider == "ollama":
        ok = _importable("ollama")
        add(Check(OK if ok else FAIL, "brain", f"Ollama, model {settings.ollama_model}", "" if ok else "pip install ollama"))
    else:
        add(Check(WARN, "brain", "no LLM configured: classic keyword mode",
                  "set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env, or install and run Ollama"))
    if online and provider in {"anthropic", "openai", "ollama"}:
        try:
            from .brain.factory import make_llm
            llm = make_llm(settings, provider)
            models = llm.list_models()
            configured = {"anthropic": settings.anthropic_model, "openai": settings.openai_model,
                          "ollama": settings.ollama_model}[provider]
            present = any(configured in m for m in models)
            add(Check(OK if present else WARN, "brain-online",
                      f"{provider} reachable, {len(models)} models; configured model {'found' if present else 'NOT found'}",
                      "" if present else f"set {provider.upper()}_MODEL to one of: {', '.join(models[:8])}"))
        except Exception as e:
            add(Check(FAIL, "brain-online", f"{provider} check failed: {e}"))

    # --- voice
    tts = settings.resolved_tts_provider()
    if tts == "elevenlabs":
        ok = _importable("elevenlabs")
        add(Check(OK if ok else FAIL, "voice", f"ElevenLabs {settings.elevenlabs_model}, voice {settings.elevenlabs_voice_name or settings.elevenlabs_voice_id}, key {settings.masked(settings.elevenlabs_api_key)}",
                  "" if ok else "pip install elevenlabs"))
        if online and ok:
            try:
                from .tts.elevenlabs_tts import ElevenLabsSpeaker
                sp = ElevenLabsSpeaker(settings)
                voices = sp.list_voices()
                add(Check(OK, "voice-online", f"ElevenLabs reachable, {len(voices)} voices available, using {sp.voice_id}"))
            except Exception as e:
                add(Check(FAIL, "voice-online", f"ElevenLabs check failed: {e}", "verify ELEVENLABS_API_KEY"))
    elif tts == "say":
        add(Check(OK if shutil.which("say") else FAIL, "voice", "macOS `say` (no ElevenLabs key set)",
                  "add ELEVENLABS_API_KEY to .env for the real voice"))
    else:
        add(Check(WARN, "voice", "console only (no audio)", "add ELEVENLABS_API_KEY or run on macOS"))

    # --- ears
    stt = settings.resolved_stt_provider()
    if stt == "whisper":
        from .stt.whisper_local import model_is_cached
        cached = model_is_cached(settings.whisper_model)
        add(Check(OK if cached else WARN, "ears", f"faster-whisper {settings.whisper_model} ({'cached' if cached else 'will download on first run'})",
                  "" if cached else "run: jarvis download-models"))
    elif stt == "google":
        ok = _importable("speech_recognition")
        add(Check(OK if ok else FAIL, "ears", "Google Web Speech via SpeechRecognition (online)",
                  "" if ok else "pip install SpeechRecognition   (or faster-whisper for local STT)"))

    # --- wake
    wake = settings.resolved_wake_mode()
    if wake == "wakeword":
        from .wake.oww import model_files_present
        present = model_files_present(settings.wake_word)
        add(Check(OK if present else WARN, "wake", f"openWakeWord '{settings.wake_word}' ({'model present' if present else 'model will download on first run'})",
                  "" if present else "run: jarvis download-models"))
    elif wake == "name":
        add(Check(WARN, "wake", "openWakeWord not installed: always-listening name mode",
                  "pip install openwakeword onnxruntime   for 'hey Jarvis'"))
    else:
        add(Check(OK, "wake", f"{wake} mode"))

    # --- audio devices
    try:
        import sounddevice as sd
    except Exception as e:  # ImportError, or OSError when the PortAudio library is missing (Linux)
        sd = None
        add(Check(FAIL, "audio", f"sounddevice unavailable: {e}",
                  "pip install sounddevice   (Linux also needs: sudo apt install libportaudio2)"))
    if sd is not None:
        try:
            dev = sd.query_devices(kind="input")
            sd.check_input_settings(device=None, channels=1, samplerate=settings.sample_rate, dtype="int16")
            add(Check(OK, "mic", f"default input: {dev['name']}"))
        except Exception as e:
            add(Check(FAIL, "mic", f"no usable input device: {e}",
                      "check System Settings > Privacy & Security > Microphone for your terminal app"))
        try:
            out = sd.query_devices(kind="output")
            add(Check(OK, "speaker", f"default output: {out['name']}"))
        except Exception as e:
            add(Check(FAIL, "speaker", f"no output device: {e}"))

    # --- skills' external tools
    add(Check(OK if _importable("ddgs") else FAIL, "web", "ddgs (DuckDuckGo search)", "pip install ddgs"))
    if platform.system() == "Darwin":
        for tool in ("open", "osascript", "mdfind", "screencapture", "pmset"):
            if not shutil.which(tool):
                add(Check(WARN, "macos", f"`{tool}` not found; some skills will not work"))
    return checks


def format_checks(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        line = f"[{c.status}] {c.area:12s} {c.detail}"
        if c.hint and c.status != OK:
            line += f"\n       -> {c.hint}"
        lines.append(line)
    fails = sum(1 for c in checks if c.status == FAIL)
    warns = sum(1 for c in checks if c.status == WARN)
    lines.append("")
    lines.append("All good. Run `jarvis` to start." if not fails and not warns
                 else f"{fails} problem(s), {warns} warning(s).")
    return "\n".join(lines)
