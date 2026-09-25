"""OpenAI adapter (Chat Completions with function calling)."""

from __future__ import annotations

import json
from typing import Any

from .base import LLM, LLMError, LLMResponse, Message, ToolCall, ToolSpec


def to_openai_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ]
            out.append(entry)
        elif m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or "(no output)"})
    return out


def to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools
    ]


class OpenAILLM(LLM):
    name = "openai"

    def __init__(self, api_key: str, model: str, max_tokens: int = 400):
        try:
            import openai
        except ImportError as e:  # pragma: no cover
            raise LLMError("The 'openai' package is not installed. Run: pip install openai") from e
        self._client = openai.OpenAI(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=to_openai_messages(system, messages),
            max_completion_tokens=self.max_tokens,
        )
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:
            raise LLMError(f"OpenAI request failed: {e}") from e

        msg = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return LLMResponse(text=(msg.content or "").strip(), tool_calls=calls)

    def list_models(self) -> list[str]:
        try:
            return sorted(m.id for m in self._client.models.list().data)
        except Exception as e:
            raise LLMError(f"Could not list OpenAI models: {e}") from e
