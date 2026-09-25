"""Build the configured brain: one provider, or the tiered router (free -> fast -> smart)."""

from __future__ import annotations

import logging

from ..config import Settings
from .base import LLM, LLMError

log = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openai", "ollama", "keyword")


def parse_spec(spec: str) -> tuple[str, str | None]:
    """'anthropic:claude-sonnet-5' -> ('anthropic', 'claude-sonnet-5'); 'keyword' -> ('keyword', None).

    Ollama tags contain a colon ('ollama:qwen3:4b'), so only the first colon splits.
    """
    spec = (spec or "").strip()
    if not spec:
        raise LLMError("Empty LLM spec.")
    provider, _, model = spec.partition(":")
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        raise LLMError(f"Unknown LLM provider '{provider}' in '{spec}'. Use one of: {', '.join(PROVIDERS)}.")
    return provider, (model.strip() or None)


def make_llm(settings: Settings, provider: str | None = None, model: str | None = None) -> LLM:
    """One concrete LLM. `provider` defaults to the single resolved provider; `model` overrides its default."""
    provider = (provider or settings.resolved_llm_provider()).lower()
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMError("Anthropic is selected but ANTHROPIC_API_KEY is not set.")
        from .anthropic_llm import AnthropicLLM
        return AnthropicLLM(settings.anthropic_api_key, model or settings.anthropic_model, settings.max_tokens)
    if provider == "openai":
        if not settings.openai_api_key:
            raise LLMError("OpenAI is selected but OPENAI_API_KEY is not set.")
        from .openai_llm import OpenAILLM
        return OpenAILLM(settings.openai_api_key, model or settings.openai_model, settings.max_tokens)
    if provider == "ollama":
        from .ollama_llm import OllamaLLM
        return OllamaLLM(model or settings.ollama_model, settings.ollama_host)
    if provider == "keyword":
        from .keyword_brain import KeywordBrain
        return KeywordBrain(settings.assistant_name)
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}'. Use auto, anthropic, openai, ollama or keyword.")


def make_llm_from_spec(settings: Settings, spec: str) -> LLM:
    provider, model = parse_spec(spec)
    return make_llm(settings, provider, model)


def make_tiered_llm(settings: Settings, pinned: str | None = None) -> LLM:
    """The free -> fast -> smart router. Identical specs share one LLM instance (so no double calls)."""
    from .rating import ComplexityRater
    from .tiered import TieredLLM

    specs = settings.resolved_tier_specs()
    instances: dict[str, LLM] = {}
    tiers: dict[str, LLM | None] = {}
    for tier, spec in specs.items():
        if spec not in instances:
            try:
                instances[spec] = make_llm_from_spec(settings, spec)
            except LLMError as e:
                log.warning("tier %s (%s) unavailable: %s", tier, spec, e)
                tiers[tier] = None
                continue
        tiers[tier] = instances[spec]
    rater = ComplexityRater(
        fast_threshold=settings.routing_fast_threshold,
        smart_threshold=settings.routing_smart_threshold,
        free_is_llm=(parse_spec(specs["free"])[0] != "keyword"),
        assistant_name=settings.assistant_name,
    )
    pin = pinned or (settings.llm_routing if settings.llm_routing in ("free", "fast", "smart") else None)
    return TieredLLM(tiers, rater, escalate=settings.routing_escalate, pinned=pin)


def make_brain(settings: Settings) -> LLM:
    """What the assistant talks to: the tiered router by default, a single provider when one is pinned."""
    if settings.uses_tiers():
        return make_tiered_llm(settings)
    return make_llm(settings)
