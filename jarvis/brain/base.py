"""Provider-neutral types for the LLM layer.

Every provider adapter (Anthropic, OpenAI, Ollama, keyword) speaks this one
interface, so the router, the skills and the tests never see a vendor SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    """A callable the model may invoke. `parameters` is a JSON Schema object."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    """One turn of conversation.

    role: "user" | "assistant" | "tool"
    - assistant turns may carry `tool_calls`
    - tool turns carry the result `content` plus `tool_call_id` / `tool_name`
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLM(ABC):
    """Minimal chat-completion-with-tools interface."""

    name: str = "llm"
    model: str = ""

    @abstractmethod
    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.name} ({self.model})" if self.model else self.name


class LLMError(RuntimeError):
    """Raised when the model provider fails (network, auth, bad model)."""
