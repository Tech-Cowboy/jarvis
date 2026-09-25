"""Build the configured LLM."""

from __future__ import annotations

import logging

from ..config import Settings
from .base import LLM, LLMError

log = logging.getLogger(__name__)


def make_llm(settings: Settings, provider: str | None = None) -> LLM:
    provider = (provider or settings.resolved_llm_provider()).lower()
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.")
        from .anthropic_llm import AnthropicLLM
        return AnthropicLLM(settings.anthropic_api_key, settings.anthropic_model, settings.max_tokens)
    if provider == "openai":
        if not settings.openai_api_key:
            raise LLMError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set.")
        from .openai_llm import OpenAILLM
        return OpenAILLM(settings.openai_api_key, settings.openai_model, settings.max_tokens)
    if provider == "ollama":
        from .ollama_llm import OllamaLLM
        return OllamaLLM(settings.ollama_model, settings.ollama_host)
    if provider == "keyword":
        from .keyword_brain import KeywordBrain
        return KeywordBrain(settings.assistant_name)
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}'. Use auto, anthropic, openai, ollama or keyword.")
