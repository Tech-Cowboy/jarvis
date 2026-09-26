"""Conversational Daxton: an ElevenLabs Conversational AI agent whose hands are Daxton's skills.

The agent runs on ElevenLabs (speech recognition, turn-taking, interruptions, the LLM, the voice);
every one of Daxton's skills is registered with it as a *client tool*, so when the agent decides to
open an app, search the web, read a file or delegate a job, the call comes back to whatever client
is holding the conversation, the Mac (daxton/convai/local.py) or the dashboard page (daxton/ui),
which runs the skill on the Mac and returns the result. Nothing about the Mac is exposed to the
internet for this; the agent only ever talks to a client that is logged in.

    daxton convai setup      create or update the agent and its tools from the current skills
    daxton convai talk       a conversation on the Mac's own microphone and speakers, right now
    daxton convai status     what exists

State (the agent id and the tool ids) lives in ~/.daxton/convai.json.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

API = "https://api.elevenlabs.io"
AGENT_TAG = "[Daxton AI]"  # marks the tools this module owns on the ElevenLabs workspace
CLIENT_TOOL_TIMEOUT = 40
LONG_TOOLS = {"delegate_task": 20, "run_command": 70, "run_python": 70, "fetch_page": 30, "web_search": 30,
              "news_search": 30, "wikipedia_summary": 30}
DYNAMIC_VARIABLES = {
    "user_name": "the user",
    "honorific": "sir",
    "now": "unknown",
    "recent_notes": "none",
    "running_tasks": "none",
    "last_conversation": "none",
    "client": "the Mac",
}


# ------------------------------------------------------------------ the agent, as data
def agent_prompt(settings, registry) -> str:
    names = ", ".join(registry.names())
    return f"""You are {settings.assistant_name}, {{{{user_name}}}}'s personal AI assistant, speaking with them in real time.
You are talking, not writing: short sentences, natural rhythm, no lists, no markdown, no URLs read aloud,
numbers and times said the way a person says them. One thought at a time; let them interrupt.

Personality: dry, competent, understated, warm; occasionally address them as "{{{{honorific}}}}". Never lecture, never pad.

You get things done through tools on their Mac, and the tools are yours to use without asking permission for
ordinary work: {names}.
- Facts, news, prices, anything current or uncertain: web_search, then fetch_page to read the source, then answer in your own words.
- Look-ups: wikipedia_summary. Time or date: current_datetime.
- The machine: open_app, quit_app, open_website, set_volume, take_screenshot, system_stats, battery_status, find_files, list_files, read_file, write_file.
- Commands and code: run_command and run_python. Say what you are about to run when it changes something; ask first if it could delete or overwrite anything of theirs.
- Big jobs (build, write, refactor, research and compile, anything that takes more than a moment): delegate_task with a full brief, tell them it has started, keep talking; task_status when they ask how it is going. You will be told when it finishes.
- Memory: remember saves a note, recall_notes reads them. set_timer for timers. end_session when they say goodbye.
After a tool returns, say what happened in one sentence. If a tool errors, say so plainly and offer the next best step.
If a request is ambiguous, ask one short question.

Context for this conversation: it is {{{{now}}}}; you are running on {{{{client}}}}.
Recent notes: {{{{recent_notes}}}}
Background tasks: {{{{running_tasks}}}}
Last conversation: {{{{last_conversation}}}}"""


def first_message(settings) -> str:
    return ""  # the user spoke first (the name woke the assistant), so the agent listens


def tool_configs(registry) -> list[dict[str, Any]]:
    """Every skill as an ElevenLabs client tool config (the JSON the tools API takes)."""
    out = []
    for spec in registry.tool_specs():
        params = _convert_schema(spec.parameters)
        out.append({
            "type": "client",
            "name": spec.name,
            "description": f"{AGENT_TAG} {spec.description.strip().splitlines()[0]}"[:1000],
            "parameters": params,
            "expects_response": True,
            "response_timeout_secs": LONG_TOOLS.get(spec.name, CLIENT_TOOL_TIMEOUT),
            "disable_interruptions": False,
        })
    return out


def _convert_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Our JSON schema subset -> the ElevenLabs object schema (same shape; arrays need items, objects properties)."""
    props = {}
    for name, prop in (schema.get("properties") or {}).items():
        p: dict[str, Any] = {"type": prop.get("type", "string")}
        if prop.get("description"):
            p["description"] = prop["description"]
        if p["type"] == "array":
            p["items"] = {"type": (prop.get("items") or {}).get("type", "string")}
        if p["type"] == "object":
            p["properties"] = {}
        props[name] = p
    out: dict[str, Any] = {"type": "object", "properties": props}
    if schema.get("required"):
        out["required"] = list(schema["required"])
    return out


def agent_body(settings, tool_ids: list[str], prompt: str) -> dict[str, Any]:
    voice = settings.convai_voice_id or settings.elevenlabs_voice_id
    return {
        "name": f"{settings.product_name} (personal)",
        "tags": ["daxton-ai"],
        "conversation_config": {
            "agent": {
                "first_message": first_message(settings),
                "language": "en",
                "dynamic_variables": {"dynamic_variable_placeholders": dict(DYNAMIC_VARIABLES)},
                "prompt": {
                    "prompt": prompt,
                    "llm": settings.convai_llm,
                    "temperature": 0.5,
                    "max_tokens": 400,
                    "tool_ids": tool_ids,
                    "enable_parallel_tool_calls": False,
                },
            },
            "tts": {
                "voice_id": voice,
                "model_id": settings.convai_tts_model,
                "stability": settings.elevenlabs_stability,
                "similarity_boost": settings.elevenlabs_similarity,
                "speed": settings.elevenlabs_speed,
            },
            "turn": {"turn_timeout": 10, "silence_end_call_timeout": settings.convai_silence_end},
            "conversation": {"max_duration_seconds": settings.convai_max_minutes * 60},
        },
        "platform_settings": {"auth": {"enable_auth": True}},
    }


# ------------------------------------------------------------------ live context (dynamic variables)
def dynamic_variables(settings, client: str = "the Mac") -> dict[str, str]:
    """What the agent should know as the conversation opens."""
    out = dict(DYNAMIC_VARIABLES)
    out["user_name"] = settings.user_name or "the user"
    out["honorific"] = settings.honorific or "sir"
    out["now"] = datetime.now().strftime("%A, %B %d, %Y, %I:%M %p").replace(" 0", " ")
    out["client"] = client
    try:
        notes = json.loads((Path(settings.data_dir) / "notes.json").read_text(encoding="utf-8"))
        if notes:
            out["recent_notes"] = "; ".join(n["text"] for n in notes[-8:])[:800]
    except Exception:
        pass
    try:
        from ..skills.work import _book

        running = [f"{t['id']}: {t['task'][:80]} ({t['status']})" for t in _book.tasks[-5:]]
        if running:
            out["running_tasks"] = "; ".join(running)[:600]
    except Exception:
        pass
    try:
        out["last_conversation"] = last_conversation_summary(settings) or "none"
    except Exception:
        pass
    return out


def conversations_dir(settings) -> Path:
    return Path(settings.data_dir) / "conversations"


def save_transcript(settings, lines: list[tuple[str, str]], client: str) -> Path | None:
    """Keep the conversation on disk so the next one can pick up where this left off."""
    if not lines:
        return None
    d = conversations_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{datetime.now():%Y%m%d-%H%M%S}-{client.replace(' ', '-')}.md"
    body = "\n".join(f"{who}: {text}" for who, text in lines)
    path.write_text(f"# Conversation on {client}, {datetime.now():%Y-%m-%d %H:%M}\n\n{body}\n", encoding="utf-8")
    return path


def last_conversation_summary(settings, max_chars: int = 700) -> str:
    d = conversations_dir(settings)
    if not d.is_dir():
        return ""
    files = sorted(d.glob("*.md"))
    if not files:
        return ""
    text = files[-1].read_text(encoding="utf-8", errors="replace")
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    tail = " ".join(lines[-8:])
    stamp = files[-1].stem[:15]
    return f"({stamp}) {tail}"[:max_chars]


# ------------------------------------------------------------------ REST client
class ConvAIError(RuntimeError):
    pass


class ElevenConvAI:
    """The few ElevenLabs Conversational AI endpoints this module needs, over httpx."""

    def __init__(self, api_key: str, base: str = API, transport=None):
        import httpx

        if not api_key:
            raise ConvAIError("ELEVENLABS_API_KEY is not set")
        self.http = httpx.Client(base_url=base, headers={"xi-api-key": api_key}, timeout=30, transport=transport)

    def _check(self, r) -> Any:
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = r.text[:400]
            raise ConvAIError(f"{r.request.method} {r.request.url.path} -> {r.status_code}: {detail}")
        return r.json() if r.content else {}

    def list_tools(self) -> list[dict[str, Any]]:
        data = self._check(self.http.get("/v1/convai/tools"))
        return list(data.get("tools") or [])

    def create_tool(self, config: dict[str, Any]) -> str:
        data = self._check(self.http.post("/v1/convai/tools", json={"tool_config": config}))
        return data["id"]

    def update_tool(self, tool_id: str, config: dict[str, Any]) -> None:
        self._check(self.http.patch(f"/v1/convai/tools/{tool_id}", json={"tool_config": config}))

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        r = self.http.get(f"/v1/convai/agents/{agent_id}")
        if r.status_code == 404:
            return None
        return self._check(r)

    def create_agent(self, body: dict[str, Any]) -> str:
        data = self._check(self.http.post("/v1/convai/agents/create", json=body))
        return data["agent_id"]

    def update_agent(self, agent_id: str, body: dict[str, Any]) -> None:
        self._check(self.http.patch(f"/v1/convai/agents/{agent_id}", json=body))

    def signed_url(self, agent_id: str) -> str:
        return self._check(self.http.get("/v1/convai/conversation/get-signed-url", params={"agent_id": agent_id}))["signed_url"]

    def webrtc_token(self, agent_id: str) -> str:
        return self._check(self.http.get("/v1/convai/conversation/token", params={"agent_id": agent_id}))["token"]


# ------------------------------------------------------------------ state and sync
def state_path(settings) -> Path:
    return Path(settings.data_dir) / "convai.json"


def load_state(settings) -> dict[str, Any]:
    try:
        return json.loads(state_path(settings).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(settings, state: dict[str, Any]) -> None:
    p = state_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def sync_agent(settings, registry, client: ElevenConvAI, printer=print) -> dict[str, Any]:
    """Create or update the tools and the agent so they match the current skills and settings."""
    state = load_state(settings)
    existing = {t.get("tool_config", {}).get("name"): t for t in client.list_tools()
                if str(t.get("tool_config", {}).get("description", "")).startswith(AGENT_TAG)}
    tool_ids: dict[str, str] = {}
    created = updated = 0
    for cfg in tool_configs(registry):
        current = existing.get(cfg["name"])
        if current:
            client.update_tool(current["id"], cfg)
            tool_ids[cfg["name"]] = current["id"]
            updated += 1
        else:
            tool_ids[cfg["name"]] = client.create_tool(cfg)
            created += 1
    printer(f"Tools: {created} created, {updated} updated ({len(tool_ids)} in all).")

    prompt = agent_prompt(settings, registry)
    body = agent_body(settings, list(tool_ids.values()), prompt)
    agent_id = settings.convai_agent_id or state.get("agent_id") or ""
    if agent_id and client.get_agent(agent_id) is not None:
        client.update_agent(agent_id, body)
        printer(f"Agent updated: {agent_id}")
    else:
        agent_id = client.create_agent(body)
        printer(f"Agent created: {agent_id}")
    state.update({"agent_id": agent_id, "tool_ids": tool_ids, "llm": settings.convai_llm,
                  "voice_id": body["conversation_config"]["tts"]["voice_id"],
                  "updated": datetime.now().isoformat(timespec="seconds")})
    save_state(settings, state)
    return state
