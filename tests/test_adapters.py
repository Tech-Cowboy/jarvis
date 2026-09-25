"""The three provider adapters must produce the exact wire shapes each API expects (no network)."""

from __future__ import annotations

import json

from jarvis.brain.anthropic_llm import to_anthropic_messages, to_anthropic_tools
from jarvis.brain.base import Message, ToolCall, ToolSpec
from jarvis.brain.ollama_llm import to_ollama_messages, to_ollama_tools
from jarvis.brain.openai_llm import to_openai_messages, to_openai_tools

TOOLS = [ToolSpec("open_app", "Open an app.", {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})]

HISTORY = [
    Message("user", "open safari and spotify"),
    Message("assistant", "On it.", tool_calls=[ToolCall("c1", "open_app", {"name": "Safari"}),
                                               ToolCall("c2", "open_app", {"name": "Spotify"})]),
    Message("tool", "Opened Safari.", tool_call_id="c1", tool_name="open_app"),
    Message("tool", "Opened Spotify.", tool_call_id="c2", tool_name="open_app"),
    Message("assistant", "Both are open."),
]


def test_anthropic_shapes():
    msgs = to_anthropic_messages(HISTORY)
    assert msgs[0] == {"role": "user", "content": "open safari and spotify"}
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][0] == {"type": "text", "text": "On it."}
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "c1", "name": "open_app", "input": {"name": "Safari"}}
    # both tool results grouped into ONE user message (API requirement)
    assert msgs[2]["role"] == "user" and len(msgs[2]["content"]) == 2
    assert msgs[2]["content"][1] == {"type": "tool_result", "tool_use_id": "c2", "content": "Opened Spotify."}
    assert msgs[3] == {"role": "assistant", "content": [{"type": "text", "text": "Both are open."}]}
    assert to_anthropic_tools(TOOLS)[0]["input_schema"]["required"] == ["name"]


def test_openai_shapes():
    msgs = to_openai_messages("SYS", HISTORY)
    assert msgs[0] == {"role": "system", "content": "SYS"}
    a = msgs[2]
    assert a["role"] == "assistant" and a["content"] == "On it."
    assert a["tool_calls"][0]["type"] == "function"
    assert json.loads(a["tool_calls"][0]["function"]["arguments"]) == {"name": "Safari"}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "Opened Safari."}
    assert msgs[4]["tool_call_id"] == "c2"
    t = to_openai_tools(TOOLS)[0]
    assert t["type"] == "function" and t["function"]["name"] == "open_app"


def test_ollama_shapes():
    msgs = to_ollama_messages("SYS", HISTORY)
    assert msgs[0]["role"] == "system"
    a = msgs[2]
    assert a["tool_calls"][0] == {"function": {"name": "open_app", "arguments": {"name": "Safari"}}}
    assert msgs[3] == {"role": "tool", "content": "Opened Safari.", "tool_name": "open_app"}
    assert to_ollama_tools(TOOLS)[0]["function"]["parameters"]["type"] == "object"


def test_empty_tool_result_is_never_blank():
    msgs = to_anthropic_messages([Message("user", "x"), Message("assistant", "", [ToolCall("c", "t", {})]),
                                  Message("tool", "", tool_call_id="c", tool_name="t")])
    assert msgs[2]["content"][0]["content"] == "(no output)"


def test_anthropic_workspace_header_and_hint():
    pytest = __import__("pytest")
    pytest.importorskip("anthropic")
    from jarvis.brain.anthropic_llm import AnthropicLLM, _explain

    plain = AnthropicLLM("sk-test", "claude-x")
    assert "anthropic-workspace-id" not in plain._client.default_headers
    scoped = AnthropicLLM("sk-test", "claude-x", workspace_id="wrkspc_123")
    assert scoped._client.default_headers["anthropic-workspace-id"] == "wrkspc_123"
    hint = _explain(RuntimeError("400: This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header"))
    assert "ANTHROPIC_WORKSPACE_ID" in hint and "wrkspc_" in hint
    assert _explain(RuntimeError("rate limited")) == "Anthropic request failed: rate limited"
