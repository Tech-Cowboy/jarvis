from __future__ import annotations

import os

import pytest

from daxton.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings(data_dir=tmp_path / "data", user_name="Test", llm_provider="keyword", tts_provider="console")
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Tests must not pick up the developer's real keys or .env."""
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "LLM_PROVIDER",
                "TTS_PROVIDER", "STT_PROVIDER", "WAKE_MODE", "OLLAMA_HOST"):
        monkeypatch.delenv(key, raising=False)
    yield
