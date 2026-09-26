"""Conversational Daxton: the agent as data, the sync against a fake ElevenLabs, the endpoints, the local audio."""

from __future__ import annotations

import json
import queue
import threading
import time

import numpy as np
import pytest

from daxton import convai
from daxton.assistant import Assistant
from daxton.brain.keyword_brain import KeywordBrain
from daxton.skills import load_default_skills
from daxton.tts.console import ConsoleSpeaker


# ------------------------------------------------------------------ the agent as data
def test_tool_configs_cover_every_skill_with_valid_shapes():
    reg = load_default_skills()
    cfgs = convai.tool_configs(reg)
    assert {c["name"] for c in cfgs} == set(reg.names())
    for c in cfgs:
        assert c["type"] == "client" and c["expects_response"] is True and c["description"].startswith(convai.AGENT_TAG)
        assert c["parameters"]["type"] == "object"
        for prop in c["parameters"]["properties"].values():
            assert prop["type"] in ("string", "integer", "number", "boolean", "array", "object")
            if prop["type"] == "array":
                assert prop["items"]["type"]
    by_name = {c["name"]: c for c in cfgs}
    assert by_name["delegate_task"]["response_timeout_secs"] == 20
    assert by_name["run_command"]["response_timeout_secs"] >= 60
    assert by_name["set_timer"]["parameters"]["required"] == ["duration"]


def test_prompt_and_agent_body(settings):
    reg = load_default_skills()
    prompt = convai.agent_prompt(settings, reg)
    assert "delegate_task" in prompt and "{{user_name}}" in prompt and "{{now}}" in prompt and "markdown" in prompt
    body = convai.agent_body(settings, ["t1", "t2"], prompt)
    agent = body["conversation_config"]["agent"]
    assert agent["prompt"]["tool_ids"] == ["t1", "t2"] and agent["prompt"]["llm"] == "claude-sonnet-5"
    assert set(agent["dynamic_variables"]["dynamic_variable_placeholders"]) == set(convai.DYNAMIC_VARIABLES)
    assert body["conversation_config"]["tts"]["voice_id"] == settings.elevenlabs_voice_id
    assert body["platform_settings"]["auth"]["enable_auth"] is True
    settings.convai_voice_id = "custom"
    assert convai.agent_body(settings, [], prompt)["conversation_config"]["tts"]["voice_id"] == "custom"


def test_dynamic_variables_and_transcript_memory(settings):
    (settings.data_dir / "notes.json").write_text(json.dumps([{"text": "the gate code is 4412", "when": "x"}]))
    dv = convai.dynamic_variables(settings, client="the phone")
    assert dv["client"] == "the phone" and "4412" in dv["recent_notes"] and dv["last_conversation"] == "none"
    assert set(dv) == set(convai.DYNAMIC_VARIABLES)
    path = convai.save_transcript(settings, [("You", "hello"), ("Daxton", "Good evening, sir.")], "the Mac")
    assert path and path.is_file() and "Good evening" in path.read_text()
    assert convai.save_transcript(settings, [], "the Mac") is None
    assert "Good evening" in convai.last_conversation_summary(settings)
    assert "Good evening" in convai.dynamic_variables(settings)["last_conversation"]


# ------------------------------------------------------------------ sync against a fake ElevenLabs
class FakeConvAI:
    def __init__(self, tools=None, agents=None):
        self.tools = list(tools or [])
        self.agents = dict(agents or {})
        self.calls: list[tuple] = []
        self._n = 0

    def list_tools(self):
        self.calls.append(("list_tools",))
        return list(self.tools)

    def create_tool(self, cfg):
        self._n += 1
        tid = f"tool_{self._n}"
        self.tools.append({"id": tid, "tool_config": cfg})
        self.calls.append(("create_tool", cfg["name"]))
        return tid

    def update_tool(self, tool_id, cfg):
        self.calls.append(("update_tool", tool_id, cfg["name"]))

    def get_agent(self, agent_id):
        self.calls.append(("get_agent", agent_id))
        return self.agents.get(agent_id)

    def create_agent(self, body):
        self.calls.append(("create_agent", body["name"]))
        self.agents["agent_new"] = body
        return "agent_new"

    def update_agent(self, agent_id, body):
        self.calls.append(("update_agent", agent_id))
        self.agents[agent_id] = body

    def signed_url(self, agent_id):
        return f"wss://signed/{agent_id}"

    def webrtc_token(self, agent_id):
        return f"token-for-{agent_id}"


def test_sync_creates_then_updates(settings):
    reg = load_default_skills()
    fake = FakeConvAI(tools=[{"id": "foreign", "tool_config": {"name": "remember", "description": "someone else's tool"}}])
    out = []
    state = convai.sync_agent(settings, reg, fake, printer=out.append)
    assert state["agent_id"] == "agent_new" and len(state["tool_ids"]) == len(reg.names())
    assert ("create_agent", "Daxton AI (personal)") in fake.calls
    # a tool with our name but not our tag is left alone: ours was created fresh
    assert state["tool_ids"]["remember"] != "foreign"
    assert json.loads(convai.state_path(settings).read_text())["agent_id"] == "agent_new"
    assert settings.resolved_convai_agent_id() == "agent_new"
    # second run: everything updates, nothing is created
    fake.calls.clear()
    convai.sync_agent(settings, reg, fake, printer=out.append)
    kinds = {c[0] for c in fake.calls}
    assert "update_agent" in kinds and "update_tool" in kinds and "create_tool" not in kinds and "create_agent" not in kinds
    assert any("0 created" in line for line in out[-2:])


def test_convai_error_from_http(settings):
    import httpx

    def handler(request):
        return httpx.Response(401, json={"detail": {"status": "invalid_api_key"}})

    client = convai.ElevenConvAI("bad", transport=httpx.MockTransport(handler))
    with pytest.raises(convai.ConvAIError, match="401"):
        client.list_tools()
    with pytest.raises(convai.ConvAIError):
        convai.ElevenConvAI("")


def test_rest_client_shapes(settings):
    import httpx

    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.url.params.get("agent_id")))
        if request.url.path == "/v1/convai/tools" and request.method == "GET":
            return httpx.Response(200, json={"tools": [{"id": "t1", "tool_config": {"name": "x", "description": "[Daxton AI] x"}}]})
        if request.url.path == "/v1/convai/tools" and request.method == "POST":
            assert json.loads(request.content)["tool_config"]["type"] == "client"
            return httpx.Response(200, json={"id": "t2"})
        if request.url.path == "/v1/convai/agents/create":
            return httpx.Response(200, json={"agent_id": "a1"})
        if request.url.path == "/v1/convai/agents/nope":
            return httpx.Response(404, json={"detail": "not found"})
        if request.url.path == "/v1/convai/conversation/token":
            return httpx.Response(200, json={"token": "tok"})
        if request.url.path == "/v1/convai/conversation/get-signed-url":
            return httpx.Response(200, json={"signed_url": "wss://x"})
        return httpx.Response(200, json={})

    c = convai.ElevenConvAI("key", transport=httpx.MockTransport(handler))
    assert c.list_tools()[0]["id"] == "t1"
    assert c.create_tool({"type": "client", "name": "y", "description": "d", "parameters": {"type": "object", "properties": {}}}) == "t2"
    assert c.create_agent({"name": "n"}) == "a1" and c.get_agent("nope") is None
    assert c.webrtc_token("a1") == "tok" and c.signed_url("a1") == "wss://x"
    assert ("GET", "/v1/convai/conversation/token", "a1") in seen
    assert c.http.headers["xi-api-key"] == "key"


# ------------------------------------------------------------------ the dashboard side
fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from daxton.ui import server as ui_server  # noqa: E402


def make(settings, agent=True):
    settings.dashboard_secret = "unit-test-secret"
    if agent:
        settings.elevenlabs_api_key = "key"
        settings.convai_agent_id = "agent_x"
    a = Assistant(settings, KeywordBrain(), ConsoleSpeaker())
    a.show_tools = False
    return a, TestClient(ui_server.create_app(a))


def test_session_endpoint_and_snapshot(settings, monkeypatch):
    a, client = make(settings)
    monkeypatch.setattr(ui_server, "convai_client_factory", lambda s: FakeConvAI())
    with client:
        snap = client.get("/api/state").json()
        assert snap["convai"] == {"ready": True, "agent_id": "agent_x", "local": True, "active": False}
        r = client.get("/api/convai/session")
        assert r.status_code == 200
        body = r.json()
        assert body["token"] == "token-for-agent_x" and body["agent_id"] == "agent_x"
        assert body["dynamic_variables"]["client"] == "the Mac's browser"

    class NoWebRtc(FakeConvAI):
        def webrtc_token(self, agent_id):
            raise RuntimeError("no webrtc")

    monkeypatch.setattr(ui_server, "convai_client_factory", lambda s: NoWebRtc())
    with client:
        body = client.get("/api/convai/session").json()
        assert "token" not in body and body["signed_url"] == "wss://signed/agent_x"

    settings.elevenlabs_api_key = ""
    settings.convai_agent_id = ""
    a2, client2 = make(settings, agent=False)
    with client2:
        assert client2.get("/api/state").json()["convai"]["ready"] is False
        assert client2.get("/api/convai/session").status_code == 404
        r = client2.post("/api/command", json={"cmd": "converse"})
        assert r.json()["ok"] is False and "convai setup" in r.json()["error"]


def test_browser_conversation_events_reach_the_log_and_the_memory(settings):
    a, client = make(settings)
    q = a.bus.subscribe()
    with client:
        assert client.post("/api/convai/event", json={"type": "start"}).json()["ok"] is True
        client.post("/api/convai/event", json={"type": "user", "text": "what time is it"})
        client.post("/api/convai/event", json={"type": "mode", "mode": "speaking"})
        client.post("/api/convai/event", json={"type": "agent", "text": "Half past four, sir."})
        r = client.post("/api/convai/event", json={"type": "end", "transcript": [
            {"who": "You", "text": "what time is it"}, {"who": "Daxton", "text": "Half past four, sir."}]})
        assert r.json()["ok"] is True
        assert client.post("/api/convai/event", json={"type": "bogus"}).status_code == 400
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    types = [e["type"] for e in events]
    assert types.count("conversation") == 2 and "user" in types and "assistant" in types
    assert a.state == "idle"
    assert "Half past four" in convai.last_conversation_summary(settings)
    end = [e for e in events if e["type"] == "conversation"][-1]
    assert end["turns"] == 2 and end["saved"]


def test_converse_command_runs_a_local_session(settings, monkeypatch):
    a, client = make(settings)
    ran = {}

    class FakeLocal:
        def __init__(self, assistant, agent_id, client_label="", opening=""):
            ran.update(agent_id=agent_id, opening=opening, label=client_label)

        def run(self):
            ran["ran"] = True
            return [("You", "hi")]

        def end(self):
            ran["ended"] = True

    import daxton.convai.local as local_mod

    monkeypatch.setattr(local_mod, "LocalConversation", FakeLocal)
    with client:
        r = client.post("/api/command", json={"cmd": "converse", "text": "what's up"})
        assert r.json()["ok"] is True
        for _ in range(30):
            if ran.get("ran"):
                break
            time.sleep(0.05)
    assert ran["ran"] and ran["agent_id"] == "agent_x" and ran["opening"] == "what's up" and ran["label"] == "the Mac"
    assert a._conversation is None
    assert a.end_conversation() is False  # nothing running any more


# ------------------------------------------------------------------ the Mac's audio interface
def test_sounddevice_audio_pump_and_half_duplex():
    from daxton.convai.local import RATE, SounddeviceAudio

    class FakeOut:
        def __init__(self):
            self.written = []

        def write(self, chunk):
            self.written.append(len(chunk))
            time.sleep(0.1)  # a real device takes 100 ms to play a 100 ms chunk

    mirrored = []
    audio = SounddeviceAudio(barge_in=False, on_output=lambda data, rate: mirrored.append((len(data), rate)))
    audio._out = FakeOut()
    audio._writer = threading.Thread(target=audio._pump, daemon=True)
    audio._writer.start()
    audio.output(b"\x00\x01" * 8000)  # 0.5 s of audio
    time.sleep(0.15)
    assert audio.speaking is True and audio._out.written and mirrored[0][1] == RATE
    audio.interrupt()  # the user talked over it: the rest is dropped
    time.sleep(0.2)
    assert sum(audio._out.written) < 16000
    # while speaking (or just after), the microphone frames are replaced by silence unless barge-in is on
    sent = []
    frame = np.full(4000, 1000, dtype=np.int16)

    def cb(indata, frames, t, status):
        f = indata[:, 0].copy()
        if not audio.barge_in and (audio.speaking or time.time() < audio._speaking_until):
            f = np.zeros_like(f)
        sent.append(f.tobytes())

    audio.speaking = True
    cb(frame.reshape(-1, 1), 4000, None, None)
    assert sent[-1] == b"\x00" * 8000
    audio.speaking = False
    audio._speaking_until = 0
    cb(frame.reshape(-1, 1), 4000, None, None)
    assert sent[-1] == frame.tobytes()
    audio._stop.set()


def test_goodbye_ends_the_conversation_not_the_assistant(settings):
    from daxton.skills.control import end_session

    a = Assistant(settings, KeywordBrain(), ConsoleSpeaker())
    assert end_session(ctx=a.ctx) == "Goodbye." and a.stop_event.is_set()  # no conversation: the old behaviour
    a.stop_event.clear()

    class Conv:
        ended = False

        def end(self):
            self.ended = True

    a._conversation = Conv()
    assert end_session(ctx=a.ctx) == "Goodbye."
    assert a._conversation.ended and not a.stop_event.is_set()
