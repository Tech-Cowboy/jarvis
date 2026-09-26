"""Settings for Daxton AI, loaded from environment variables and .env files.

Load order (later wins):
  1. ~/.config/daxton/.env   (global, optional)
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

GLOBAL_ENV = Path.home() / ".config" / "daxton" / ".env"
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
    assistant_name: str = "Daxton"
    product_name: str = "Daxton AI"
    assistant_aliases: str = ""  # comma-separated other spellings the speech engine produces, e.g. "Dax, Dexton"
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
    wake_word: str = "auto"  # auto | a pre-trained openWakeWord name (hey_jarvis) | path to a custom .onnx model
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

    # Dashboard and the portal (remote access)
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8765
    dashboard_password: str = ""  # empty: the dashboard answers the Mac only; set: login required everywhere
    dashboard_secret: str = ""  # cookie signing secret; empty: generated once into ~/.daxton/dashboard.secret
    dashboard_session_days: int = 30
    public_hostname: str = ""  # e.g. daxton.example.com, the name the Cloudflare tunnel publishes
    tunnel_name: str = "daxton"  # the cloudflared tunnel name

    # Conversations (ElevenLabs Conversational AI)
    convai_agent_id: str = ""  # set by `daxton convai setup` (kept in ~/.daxton/convai.json) or CONVAI_AGENT_ID
    convai_llm: str = "claude-sonnet-5"  # the model ElevenLabs runs for the agent
    convai_voice_id: str = ""  # defaults to ELEVENLABS_VOICE_ID
    convai_tts_model: str = "eleven_flash_v2"  # English agents must use turbo or flash v2
    convai_local: bool = True  # saying the name at the Mac starts a conversation session on its own mic and speakers
    convai_barge_in: bool = False  # Mac mic stays muted while Daxton speaks (no echo cancellation without a headset)
    convai_silence_end: int = 45  # seconds of silence before a session ends by itself
    convai_max_minutes: int = 30

    # Work
    shell_enabled: bool = True  # run_command, run_python, delegate_task
    claude_code_bin: str = ""  # path to the Claude Code executable when it is not on PATH
    tasks_dir: Path = field(default_factory=lambda: Path.home() / "Documents/Claude/Projects/daxton-tasks")
    task_timeout_minutes: int = 30

    # Business systems (read-only): bookings, customers, leads, sales, the inbox and reminders
    business_name: str = ""  # how the business is referred to in speech, e.g. "Ocean View Stables"
    business_tz: str = ""  # IANA zone for bookings and reports; empty = this computer's zone
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_username: str = ""
    odoo_api_key: str = ""
    odoo_credentials_path: str = ""  # a JSON file {url, db, username, api_key}, the same one other tools use

    # Skills
    search_max_results: int = 5
    data_dir: Path = field(default_factory=lambda: Path.home() / ".daxton")

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

    @property
    def aliases(self) -> list[str]:
        return [a.strip() for a in self.assistant_aliases.split(",") if a.strip()]

    def resolved_wake_word(self) -> str:
        """The openWakeWord model to use: a pre-trained name, a custom model path, or '' when none fits the name."""
        if self.wake_word != "auto":
            return self.wake_word
        candidate = "hey_" + self.assistant_name.strip().lower().replace(" ", "_")
        try:
            import openwakeword
            return candidate if candidate in openwakeword.MODELS else ""
        except Exception:
            return ""

    def resolved_wake_mode(self) -> str:
        """wakeword when a model exists for the name (or was configured), else always-listening name mode."""
        if self.wake_mode != "auto":
            return self.wake_mode
        if not self.resolved_wake_word():
            return "name"
        try:
            import openwakeword  # noqa: F401
            return "wakeword"
        except Exception:
            return "name"

    @property
    def wake_phrase(self) -> str:
        """What to say: 'hey jarvis' for a wake model, or just the name in a sentence."""
        if self.resolved_wake_mode() == "wakeword":
            word = Path(self.resolved_wake_word()).stem if self.resolved_wake_word() else ""
            word = word.replace("_v0.1", "").replace("_", " ")
            return word or f"hey {self.assistant_name.lower()}"
        return self.assistant_name

    def convai_ready(self) -> bool:
        """A conversation agent exists and the ElevenLabs key is present."""
        return bool(self.elevenlabs_api_key) and bool(self.resolved_convai_agent_id())

    def resolved_convai_agent_id(self) -> str:
        if self.convai_agent_id:
            return self.convai_agent_id
        try:
            import json

            state = json.loads((self.data_dir / "convai.json").read_text(encoding="utf-8"))
            return str(state.get("agent_id") or "")
        except Exception:
            return ""

    def business_configured(self) -> bool:
        """Credentials for the business system are present (env, or a credentials file that exists)."""
        from .business.odoo import configured

        return configured(self)

    @property
    def business_label(self) -> str:
        return self.business_name or "the business"

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
        assistant_name=e("ASSISTANT_NAME", "Daxton"),
        product_name=e("PRODUCT_NAME", "Daxton AI"),
        assistant_aliases=e("ASSISTANT_ALIASES", ""),
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
        wake_word=e("WAKE_WORD", "auto").strip(),
        wake_threshold=_float(e("WAKE_THRESHOLD"), 0.5),
        wake_ack=e("WAKE_ACK", "phrase").strip().lower(),
        wake_ack_text=e("WAKE_ACK_TEXT", "Yes, sir?"),
        mic_device=e("MIC_DEVICE", ""),
        sample_rate=_int(e("SAMPLE_RATE"), 16000),
        silence_seconds=_float(e("SILENCE_SECONDS"), 1.2),
        max_utterance_seconds=_float(e("MAX_UTTERANCE_SECONDS"), 15.0),
        listen_timeout_seconds=_float(e("LISTEN_TIMEOUT_SECONDS"), 8.0),
        min_speech_rms=_float(e("MIN_SPEECH_RMS"), 0.010),
        dashboard_host=e("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1",
        dashboard_port=_int(e("DASHBOARD_PORT"), 8765),
        dashboard_password=e("DASHBOARD_PASSWORD", ""),
        dashboard_secret=e("DASHBOARD_SECRET", ""),
        dashboard_session_days=_int(e("DASHBOARD_SESSION_DAYS"), 30),
        public_hostname=e("PUBLIC_HOSTNAME", "").strip().lower().rstrip("."),
        tunnel_name=e("TUNNEL_NAME", "daxton").strip() or "daxton",
        convai_agent_id=e("CONVAI_AGENT_ID", "").strip(),
        convai_llm=e("CONVAI_LLM", "claude-sonnet-5").strip(),
        convai_voice_id=e("CONVAI_VOICE_ID", "").strip(),
        convai_tts_model=e("CONVAI_TTS_MODEL", "eleven_flash_v2").strip(),
        convai_local=_bool(e("CONVAI_LOCAL"), True),
        convai_barge_in=_bool(e("CONVAI_BARGE_IN"), False),
        convai_silence_end=_int(e("CONVAI_SILENCE_END"), 45),
        convai_max_minutes=_int(e("CONVAI_MAX_MINUTES"), 30),
        shell_enabled=_bool(e("DAXTON_SHELL"), True),
        claude_code_bin=e("CLAUDE_CODE_BIN", "").strip(),
        tasks_dir=Path(e("TASKS_DIR", str(Path.home() / "Documents/Claude/Projects/daxton-tasks"))).expanduser(),
        task_timeout_minutes=_int(e("TASK_TIMEOUT_MINUTES"), 30),
        business_name=e("BUSINESS_NAME", "").strip(),
        business_tz=e("BUSINESS_TZ", "").strip(),
        odoo_url=e("ODOO_URL", "").strip(),
        odoo_db=e("ODOO_DB", "").strip(),
        odoo_username=(e("ODOO_USERNAME", "") or e("ODOO_USER", "")).strip(),
        odoo_api_key=e("ODOO_API_KEY", "").strip(),
        odoo_credentials_path=e("ODOO_CREDENTIALS_PATH", "").strip(),
        search_max_results=_int(e("SEARCH_MAX_RESULTS"), 5),
        data_dir=Path(e("DAXTON_DATA_DIR", str(Path.home() / ".daxton"))).expanduser(),
        log_level=e("LOG_LEVEL", "WARNING").upper(),
        env_files=env_files,
    )
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
