"""Settings for JARVIS, loaded from environment variables and .env files.

Load order (later wins):
  1. ~/.config/jarvis/.env   (global, optional)
  2. ./.env                  (project-local, optional)
  3. real environment variables

Secrets (API keys) are only ever read from the environment. They are never
written to disk by this program and never logged.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

GLOBAL_ENV = Path.home() / ".config" / "jarvis" / ".env"
LOCAL_ENV = Path.cwd() / ".env"


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def load_env_files() -> list[Path]:
    """Load .env files into os.environ (without overriding real env vars). Returns the files found."""
    found: list[Path] = []
    for path in (GLOBAL_ENV, LOCAL_ENV):
        if path.is_file():
            load_dotenv(path, override=False)
            found.append(path)
    return found


@dataclass
class Settings:
    # Identity
    assistant_name: str = "Jarvis"
    user_name: str = ""
    honorific: str = "sir"

    # Brain (LLM)
    llm_provider: str = "auto"  # auto | anthropic | openai | ollama | keyword  (pins one provider, no tiers)
    anthropic_api_key: str = ""
    anthropic_workspace_id: str = ""  # only for organization-level keys (wrkspc_...)
    anthropic_model: str = "claude-haiku-4-5-20251001"  # fast tier
    anthropic_smart_model: str = "claude-sonnet-5"  # smart tier
    openai_api_key: str = ""
    openai_model: str = "gpt-6-luna"  # fast tier
    openai_smart_model: str = "gpt-6-sol"  # smart tier
    ollama_model: str = "qwen3:4b"  # free tier (local)
    ollama_smart_model: str = ""  # optional bigger local model for the smart tier when no hosted key is set
    ollama_host: str = ""
    max_tokens: int = 400
    history_turns: int = 8
    max_tool_rounds: int = 6

    # Tiered routing (complexity rating)
    llm_routing: str = "auto"  # auto | free | fast | smart (pin a tier) | off (single provider)
    llm_tier_free: str = "auto"  # provider[:model] e.g. ollama:qwen3:4b, keyword
    llm_tier_fast: str = "auto"  # e.g. anthropic:claude-haiku-4-5-20251001, openai:gpt-6-luna
    llm_tier_smart: str = "auto"  # e.g. anthropic:claude-sonnet-5, anthropic:claude-opus-5-5, openai:gpt-6-sol
    routing_fast_threshold: float = 0.30
    routing_smart_threshold: float = 0.60
    routing_escalate: bool = True  # move up a tier when the chosen tier fails or cannot handle the request

    # Voice (TTS)
    tts_provider: str = "auto"  # auto | elevenlabs | say | console
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "onwK4e9ZLuTAKqWW03F9"  # "Daniel" (stock voice)
    elevenlabs_voice_name: str = ""  # resolved by name if set, overrides voice_id
    elevenlabs_model: str = "eleven_flash_v2_5"
    elevenlabs_output_format: str = "pcm_24000"
    elevenlabs_stability: float = 0.5
    elevenlabs_similarity: float = 0.75
    elevenlabs_style: float = 0.0
    elevenlabs_speed: float = 1.0
    tts_cache: bool = True
    say_voice: str = ""  # macOS `say -v` voice; empty = system default

    # Ears (STT)
    stt_provider: str = "auto"  # auto | whisper | google
    whisper_model: str = "base.en"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    whisper_language: str = "en"

    # Wake
    wake_mode: str = "auto"  # auto | wakeword | name | push_to_talk
    wake_word: str = "hey_jarvis"
    wake_threshold: float = 0.5
    wake_ack: str = "phrase"  # phrase | chime | none
    wake_ack_text: str = "Yes, sir?"

    # Audio
    mic_device: str = ""  # index or name substring; empty = system default
    sample_rate: int = 16000
    silence_seconds: float = 1.2
    max_utterance_seconds: float = 15.0
    listen_timeout_seconds: float = 8.0
    min_speech_rms: float = 0.010

    # Skills
    search_max_results: int = 5
    data_dir: Path = field(default_factory=lambda: Path.home() / ".jarvis")

    # Misc
    log_level: str = "WARNING"
    env_files: list[Path] = field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    @property
    def os_name(self) -> str:
        return {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(platform.system(), platform.system())

    @property
    def is_macos(self) -> bool:
        return platform.system() == "Darwin"

    def ollama_reachable(self) -> bool:
        """Is a local Ollama server answering? Checked once per Settings instance."""
        if not hasattr(self, "_ollama_ok"):
            self._ollama_ok = _ollama_reachable(self.ollama_host)
        return self._ollama_ok

    def resolved_llm_provider(self) -> str:
        """Pick a single LLM provider: explicit setting, else first one with credentials, else keyword mode."""
        if self.llm_provider != "auto":
            return self.llm_provider
        if self.anthropic_api_key:
            return "anthropic"
        if self.openai_api_key:
            return "openai"
        if self.ollama_reachable():
            return "ollama"
        return "keyword"

    def uses_tiers(self) -> bool:
        """Tiered routing is on unless a single provider was pinned or routing is off."""
        return self.llm_provider == "auto" and self.llm_routing != "off"

    def resolved_tier_specs(self) -> dict[str, str]:
        """provider[:model] for each tier. 'auto' fills in from the keys present.

        free  : a local Ollama model when one is running, else keyword rules (no model at all)
        fast  : Claude Haiku, else the OpenAI fast model, else the free tier
        smart : Claude Sonnet, else the OpenAI smart model, else a bigger Ollama model, else the fast tier
        """
        ollama_ok = self.ollama_reachable()
        free = self.llm_tier_free
        if free == "auto":
            free = f"ollama:{self.ollama_model}" if ollama_ok else "keyword"
        fast = self.llm_tier_fast
        if fast == "auto":
            if self.anthropic_api_key:
                fast = f"anthropic:{self.anthropic_model}"
            elif self.openai_api_key:
                fast = f"openai:{self.openai_model}"
            else:
                fast = free
        smart = self.llm_tier_smart
        if smart == "auto":
            if self.anthropic_api_key:
                smart = f"anthropic:{self.anthropic_smart_model}"
            elif self.openai_api_key:
                smart = f"openai:{self.openai_smart_model}"
            elif ollama_ok and self.ollama_smart_model:
                smart = f"ollama:{self.ollama_smart_model}"
            else:
                smart = fast
        return {"free": free, "fast": fast, "smart": smart}

    def resolved_tts_provider(self) -> str:
        if self.tts_provider != "auto":
            return self.tts_provider
        if self.elevenlabs_api_key:
            return "elevenlabs"
        if self.is_macos:
            return "say"
        return "console"

    def resolved_stt_provider(self) -> str:
        if self.stt_provider != "auto":
            return self.stt_provider
        try:
            import faster_whisper  # noqa: F401
            return "whisper"
        except Exception:
            return "google"

    def resolved_wake_mode(self) -> str:
        if self.wake_mode != "auto":
            return self.wake_mode
        try:
            import openwakeword  # noqa: F401
            return "wakeword"
        except Exception:
            return "name"

    def masked(self, value: str) -> str:
        if not value:
            return "(unset)"
        return value[:4] + "…" + value[-2:] if len(value) > 8 else "set"


def _ollama_reachable(host: str) -> bool:
    """True if an Ollama server answers on the configured host (default localhost:11434)."""
    import httpx

    base = host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    if not base.startswith("http"):
        base = "http://" + base
    try:
        r = httpx.get(base.rstrip("/") + "/api/tags", timeout=0.6)
        return r.status_code == 200
    except Exception:
        return False


def load_settings() -> Settings:
    env_files = load_env_files()
    e = os.environ.get
    s = Settings(
        assistant_name=e("ASSISTANT_NAME", "Jarvis"),
        user_name=e("USER_NAME", ""),
        honorific=e("HONORIFIC", "sir"),
        llm_provider=e("LLM_PROVIDER", "auto").strip().lower(),
        anthropic_api_key=e("ANTHROPIC_API_KEY", ""),
        anthropic_workspace_id=e("ANTHROPIC_WORKSPACE_ID", "").strip(),
        anthropic_model=e("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        anthropic_smart_model=e("ANTHROPIC_SMART_MODEL", "claude-sonnet-5"),
        openai_api_key=e("OPENAI_API_KEY", ""),
        openai_model=e("OPENAI_MODEL", "gpt-6-luna"),
        openai_smart_model=e("OPENAI_SMART_MODEL", "gpt-6-sol"),
        ollama_model=e("OLLAMA_MODEL", "qwen3:4b"),
        ollama_smart_model=e("OLLAMA_SMART_MODEL", ""),
        ollama_host=e("OLLAMA_HOST", ""),
        max_tokens=_int(e("MAX_TOKENS"), 400),
        history_turns=_int(e("HISTORY_TURNS"), 8),
        max_tool_rounds=_int(e("MAX_TOOL_ROUNDS"), 6),
        llm_routing=e("LLM_ROUTING", "auto").strip().lower(),
        llm_tier_free=e("LLM_TIER_FREE", "auto").strip(),
        llm_tier_fast=e("LLM_TIER_FAST", "auto").strip(),
        llm_tier_smart=e("LLM_TIER_SMART", "auto").strip(),
        routing_fast_threshold=_float(e("ROUTING_FAST_THRESHOLD"), 0.30),
        routing_smart_threshold=_float(e("ROUTING_SMART_THRESHOLD"), 0.60),
        routing_escalate=_bool(e("ROUTING_ESCALATE"), True),
        tts_provider=e("TTS_PROVIDER", "auto").strip().lower(),
        elevenlabs_api_key=e("ELEVENLABS_API_KEY", "") or e("ELEVEN_API_KEY", ""),
        elevenlabs_voice_id=e("ELEVENLABS_VOICE_ID", "onwK4e9ZLuTAKqWW03F9"),
        elevenlabs_voice_name=e("ELEVENLABS_VOICE_NAME", ""),
        elevenlabs_model=e("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
        elevenlabs_output_format=e("ELEVENLABS_OUTPUT_FORMAT", "pcm_24000"),
        elevenlabs_stability=_float(e("ELEVENLABS_STABILITY"), 0.5),
        elevenlabs_similarity=_float(e("ELEVENLABS_SIMILARITY"), 0.75),
        elevenlabs_style=_float(e("ELEVENLABS_STYLE"), 0.0),
        elevenlabs_speed=_float(e("ELEVENLABS_SPEED"), 1.0),
        tts_cache=_bool(e("TTS_CACHE"), True),
        say_voice=e("SAY_VOICE", ""),
        stt_provider=e("STT_PROVIDER", "auto").strip().lower(),
        whisper_model=e("WHISPER_MODEL", "base.en"),
        whisper_device=e("WHISPER_DEVICE", "cpu"),
        whisper_compute_type=e("WHISPER_COMPUTE_TYPE", "int8"),
        whisper_language=e("WHISPER_LANGUAGE", "en"),
        wake_mode=e("WAKE_MODE", "auto").strip().lower(),
        wake_word=e("WAKE_WORD", "hey_jarvis"),
        wake_threshold=_float(e("WAKE_THRESHOLD"), 0.5),
        wake_ack=e("WAKE_ACK", "phrase").strip().lower(),
        wake_ack_text=e("WAKE_ACK_TEXT", "Yes, sir?"),
        mic_device=e("MIC_DEVICE", ""),
        sample_rate=_int(e("SAMPLE_RATE"), 16000),
        silence_seconds=_float(e("SILENCE_SECONDS"), 1.2),
        max_utterance_seconds=_float(e("MAX_UTTERANCE_SECONDS"), 15.0),
        listen_timeout_seconds=_float(e("LISTEN_TIMEOUT_SECONDS"), 8.0),
        min_speech_rms=_float(e("MIN_SPEECH_RMS"), 0.010),
        search_max_results=_int(e("SEARCH_MAX_RESULTS"), 5),
        data_dir=Path(e("JARVIS_DATA_DIR", str(Path.home() / ".jarvis"))).expanduser(),
        log_level=e("LOG_LEVEL", "WARNING").upper(),
        env_files=env_files,
    )
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
