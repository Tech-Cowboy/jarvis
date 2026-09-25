"""`daxton doctor`: check every part of the pipeline and say what to fix."""

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
    if settings.uses_tiers():
        specs = settings.resolved_tier_specs()
        distinct = len(set(specs.values()))
        detail = f"tiered routing: free={specs['free']}  fast={specs['fast']}  smart={specs['smart']}"
        if distinct == 1 and specs["free"] == "keyword":
            add(Check(WARN, "routing", detail, "add ANTHROPIC_API_KEY or OPENAI_API_KEY for the fast and smart tiers"))
        elif distinct == 1:
            add(Check(WARN, "routing", detail, "all tiers use the same model; set LLM_TIER_SMART for a stronger one"))
        else:
            add(Check(OK, "routing", detail))
        if not settings.ollama_reachable():
            add(Check(WARN, "free-tier", "Ollama not running: free tier is keyword rules only",
                      f"brew install ollama && ollama pull {settings.ollama_model}   (free local model for simple requests)"))
        else:
            add(Check(OK, "free-tier", f"Ollama reachable ({settings.ollama_model})"))
    if provider == "anthropic":
        ok = _importable("anthropic")
        ws = f", workspace {settings.anthropic_workspace_id}" if settings.anthropic_workspace_id else ""
        add(Check(OK if ok else FAIL, "brain", f"Anthropic, model {settings.anthropic_model}, key {settings.masked(settings.anthropic_api_key)}{ws}",
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
    if online:
        # Every provider:model the brain may use (all tiers, or the single provider), checked against the live model list.
        from .brain.factory import make_llm, parse_spec

        wanted: dict[str, set[str]] = {}
        if settings.uses_tiers():
            for spec in settings.resolved_tier_specs().values():
                prov, model = parse_spec(spec)
                if prov != "keyword":
                    wanted.setdefault(prov, set()).add(model or "")
        elif provider != "keyword":
            wanted[provider] = {""}
        for prov, models_wanted in wanted.items():
            try:
                llm = make_llm(settings, prov)
                available = llm.list_models()
            except Exception as e:
                add(Check(FAIL, "brain-online", f"{prov} check failed: {e}"))
                continue
            names = sorted(m or llm.model for m in models_wanted)
            missing = [m for m in names if not any(m in a for a in available)]
            if missing:
                add(Check(WARN, "brain-online", f"{prov} reachable, {len(available)} models; NOT found: {', '.join(missing)}",
                          f"pick from: {', '.join(available[:8])}"))
            else:
                add(Check(OK, "brain-online", f"{prov} reachable; models found: {', '.join(names)}"))

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
                  "" if cached else "run: daxton download-models"))
    elif stt == "google":
        ok = _importable("speech_recognition")
        add(Check(OK if ok else FAIL, "ears", "Google Web Speech via SpeechRecognition (online)",
                  "" if ok else "pip install SpeechRecognition   (or faster-whisper for local STT)"))

    # --- wake
    wake = settings.resolved_wake_mode()
    if wake == "wakeword":
        from .wake.oww import model_files_present
        word = settings.resolved_wake_word()
        present = model_files_present(word)
        add(Check(OK if present else WARN, "wake", f"openWakeWord '{word}' ({'model present' if present else 'model will download on first run'}); say '{settings.wake_phrase}'",
                  "" if present else "run: daxton download-models"))
    elif wake == "name":
        name = settings.assistant_name
        oww = _importable("openwakeword")
        add(Check(OK, "wake", f"always listening: say '{name}' anywhere in a sentence (no wake-word model is trained for this name)",
                  f"optional: train a 'hey {name.lower()}' model with openWakeWord and set WAKE_WORD=/path/to/hey_{name.lower()}.onnx"
                  + ("" if oww else "; pip install openwakeword onnxruntime")))
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

    # --- dashboard and portal
    ui_ok = _importable("fastapi") and _importable("uvicorn")
    add(Check(OK if ui_ok else WARN, "dashboard", f"`daxton ui` on http://{settings.dashboard_host}:{settings.dashboard_port}"
              if ui_ok else "fastapi/uvicorn missing: no dashboard", "" if ui_ok else "pip install 'daxton-ai[ui]'"))
    if settings.public_hostname or settings.dashboard_password:
        from .tunnel import CLOUDFLARED_DIR, find_cloudflared

        if not settings.dashboard_password:
            add(Check(FAIL, "portal", f"PUBLIC_HOSTNAME={settings.public_hostname} but no DASHBOARD_PASSWORD: remote "
                      "visitors are refused", "set DASHBOARD_PASSWORD in .env (12+ characters)"))
        elif len(settings.dashboard_password) < 12:
            add(Check(WARN, "portal", "DASHBOARD_PASSWORD is shorter than 12 characters",
                      "use a long passphrase; it is the second lock behind Cloudflare Access"))
        else:
            add(Check(OK, "portal", "login required for every page, API call and socket (DASHBOARD_PASSWORD set)"))
        if settings.public_hostname:
            config = CLOUDFLARED_DIR / "config.yml"
            if not find_cloudflared():
                add(Check(WARN, "tunnel", f"cloudflared not installed; https://{settings.public_hostname} is not published",
                          f"daxton tunnel setup {settings.public_hostname}"))
            elif not config.is_file() or settings.public_hostname not in config.read_text(encoding="utf-8"):
                add(Check(WARN, "tunnel", f"no cloudflared config for {settings.public_hostname}",
                          f"daxton tunnel setup {settings.public_hostname}"))
            else:
                add(Check(OK, "tunnel", f"https://{settings.public_hostname} -> http://127.0.0.1:{settings.dashboard_port} "
                          f"({config})"))
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
    lines.append("All good. Run `daxton` to start." if not fails and not warns
                 else f"{fails} problem(s), {warns} warning(s).")
    return "\n".join(lines)
