from __future__ import annotations

import numpy as np

from jarvis import config
from jarvis.audio.mic import rms
from jarvis.audio.chime import chime_samples
from jarvis.stt import clean_transcript
from jarvis.tts import clean_for_speech
from jarvis.tts.console import ConsoleSpeaker


def test_settings_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSISTANT_NAME", "Friday")
    monkeypatch.setenv("LLM_PROVIDER", "Anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-1234567890")
    monkeypatch.setenv("WAKE_THRESHOLD", "0.7")
    monkeypatch.setenv("TTS_CACHE", "no")
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setattr(config, "GLOBAL_ENV", tmp_path / "nope1")
    monkeypatch.setattr(config, "LOCAL_ENV", tmp_path / "nope2")
    s = config.load_settings()
    assert s.assistant_name == "Friday"
    assert s.llm_provider == "anthropic" and s.resolved_llm_provider() == "anthropic"
    assert s.wake_threshold == 0.7 and s.tts_cache is False
    assert (tmp_path / "d").is_dir()
    assert s.masked(s.anthropic_api_key) == "sk-a…90"
    assert s.masked("") == "(unset)"


def test_dotenv_file_is_loaded(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("USER_NAME=Zach\nELEVENLABS_API_KEY=el-test-key-000000\n")
    monkeypatch.setattr(config, "GLOBAL_ENV", tmp_path / "nope")
    monkeypatch.setattr(config, "LOCAL_ENV", env)
    monkeypatch.delenv("USER_NAME", raising=False)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "d"))
    s = config.load_settings()
    assert s.user_name == "Zach" and s.env_files == [env]
    assert s.resolved_tts_provider() == "elevenlabs"


def test_auto_provider_falls_back_to_keyword(monkeypatch):
    monkeypatch.setattr(config, "_ollama_reachable", lambda host: False)
    s = config.Settings()
    assert s.resolved_llm_provider() == "keyword"
    s.openai_api_key = "x"
    assert s.resolved_llm_provider() == "openai"
    s.anthropic_api_key = "y"
    assert s.resolved_llm_provider() == "anthropic"


def test_clean_for_speech():
    text = "**Bold** and `code`.\n\n- item one\nSee https://www.example.com/path?x=1 for [details](https://x.y)."
    assert clean_for_speech(text) == "Bold and code. item one See example.com for details."
    assert clean_for_speech("   ") == ""


def test_clean_transcript_drops_hallucinations():
    assert clean_transcript("  Thank you.  ") == ""
    assert clean_transcript("open   safari") == "open safari"


def test_console_speaker_records(capsys):
    sp = ConsoleSpeaker("Jarvis")
    sp.say("Hello **there**")
    assert sp.spoken == ["Hello there"]
    assert "Jarvis: Hello there" in capsys.readouterr().out


def test_rms_and_chime():
    silence = np.zeros(1280, dtype=np.int16)
    loud = (np.sin(np.linspace(0, 100, 1280)) * 16000).astype(np.int16)
    assert rms(silence) == 0.0 and 0.3 < rms(loud) < 0.4
    c = chime_samples(24000)
    assert c.dtype == np.float32 and len(c) > 24000 * 0.25 and np.abs(c).max() <= 0.3
