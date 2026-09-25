"""The brain: provider-neutral LLM types, adapters, and the tool-calling router.

Only the base types are imported here to keep package imports cycle-free;
use `from jarvis.brain.factory import make_llm` and `from jarvis.brain.router import Router`.
"""

from .base import LLM, LLMError, LLMResponse, Message, ToolCall, ToolSpec

__all__ = ["LLM", "LLMError", "LLMResponse", "Message", "ToolCall", "ToolSpec"]
