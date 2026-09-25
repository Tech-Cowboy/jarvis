"""Ollama adapter (local models with tool calling, e.g. qwen3, llama3.1)."""

from __future__ import annotations

from typing import Any

from .base import LLM, LLMError, LLMResponse, Message, ToolCall, ToolSpec


def to_ollama_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.content or ""}
            if m.tool_calls:
                entry["tool_calls"] = [{"function": {"name": tc.name, "arguments": tc.arguments}} for tc in m.tool_calls]
            out.append(entry)
        elif m.role == "tool":
            out.append({"role": "tool", "content": m.content or "(no output)", "tool_name": m.tool_name or ""})
    return out


def to_ollama_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools
    ]


class OllamaLLM(LLM):
    name = "ollama"

    def __init__(self, model: str, host: str = ""):
        try:
            import ollama
        except ImportError as e:  # pragma: no cover
            raise LLMError("The 'ollama' package is not installed. Run: pip install ollama") from e
        self._client = ollama.Client(host=host or None)
        self.model = model

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        kwargs: dict[str, Any] = dict(model=self.model, messages=to_ollama_messages(system, messages))
        if tools:
            kwargs["tools"] = to_ollama_tools(tools)
        try:
            try:
                resp = self._client.chat(think=False, **kwargs)  # skip "thinking" for snappy voice replies
            except TypeError:  # older ollama client without `think`
                resp = self._client.chat(**kwargs)
        except Exception as e:
            raise LLMError(f"Ollama request failed ({self.model}): {e}") from e

        msg = resp.message
        calls: list[ToolCall] = []
        for i, tc in enumerate(msg.tool_calls or []):
            calls.append(ToolCall(id=f"call_{i}", name=tc.function.name, arguments=dict(tc.function.arguments or {})))
        return LLMResponse(text=(msg.content or "").strip(), tool_calls=calls)

    def list_models(self) -> list[str]:
        try:
            return [m.model for m in self._client.list().models]
        except Exception as e:
            raise LLMError(f"Could not reach Ollama: {e}") from e
