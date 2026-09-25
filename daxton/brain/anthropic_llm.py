"""Anthropic Claude adapter (native tool use)."""

from __future__ import annotations

from typing import Any

from .base import LLM, LLMError, LLMResponse, Message, ToolCall, ToolSpec


def to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert neutral history to Anthropic's content-block format.

    Consecutive tool results are grouped into a single user message, which the
    API requires when several tools were called in one assistant turn.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content})
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments})
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        elif m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content or "(no output)"}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                    and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def to_anthropic_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, max_tokens: int = 400, workspace_id: str = ""):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMError("The 'anthropic' package is not installed. Run: pip install anthropic") from e
        # An organization-level key (one not created inside a workspace) must name the workspace on every request.
        headers = {"anthropic-workspace-id": workspace_id} if workspace_id else None
        self._client = anthropic.Anthropic(api_key=api_key, default_headers=headers)
        self.model = model
        self.max_tokens = max_tokens
        self.workspace_id = workspace_id

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=to_anthropic_messages(messages),
        )
        if tools:
            kwargs["tools"] = to_anthropic_tools(tools)
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as e:
            raise LLMError(_explain(e)) from e

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            btype = getattr(block, "type", "")
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        return LLMResponse(text="".join(text_parts).strip(), tool_calls=calls)

    def list_models(self) -> list[str]:
        try:
            return [m.id for m in self._client.models.list(limit=50).data]
        except Exception as e:
            raise LLMError(_explain(e)) from e


WORKSPACE_HINT = ("This key is an organization-level key, so Anthropic needs to know the workspace: set "
                  "ANTHROPIC_WORKSPACE_ID in .env to the workspace ID (Console > Settings > Workspaces, it starts with "
                  "wrkspc_), or create the key inside a workspace instead.")


def _explain(error: Exception) -> str:
    text = str(error)
    if "anthropic-workspace-id" in text:
        return "Anthropic request failed: " + WORKSPACE_HINT
    return f"Anthropic request failed: {text}"
