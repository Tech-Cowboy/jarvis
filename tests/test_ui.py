from __future__ import annotations

import queue
import threading

import numpy as np
import pytest

from daxton.assistant import Assistant
from daxton.audio.analysis import N_BANDS, analyze, pcm16_bytes_to_samples
from daxton.brain.keyword_brain import KeywordBrain
from daxton.events import EventBus
from daxton.tts.console import ConsoleSpeaker
from daxton.wake.simple import AlwaysListening, PushToTalk


# ------------------------------------------------------------------ bus
def test_event_bus_publishes_to_every_subscriber_and_keeps_history():
    bus = EventBus(history=3)
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish("user", text="hi")
    bus.publish("audio", rms=0.1)  # transient: not in history
    for q in (a, b):
        assert q.get_nowait()["type"] == "user" and q.get_nowait()["type"] == "audio"
    assert [e["type"] for e in bus.history] == ["user"]
    for i in range(5):
        bus.publish("notice", text=str(i))
    assert [e["text"] for e in bus.history] == ["2", "3", "4"]
    for q in (a, b):
        while not q.empty():
            q.get_nowait()
    bus.unsubscribe(a)
    bus.publish("state", state="idle")
    assert a.empty() and b.get_nowait()["state"] == "idle"
    assert bus.subscribers == 1


def test_event_bus_never_blocks_on_a_full_subscriber():
    bus = EventBus(queue_size=2)
    q = bus.subscribe()
    for i in range(10):
        bus.publish("notice", text=str(i))
    assert q.qsize() == 2


# ------------------------------------------------------------ analysis
def test_analyze_silence_and_tone():
    rms, bands = analyze(np.zeros(1280, dtype=np.int16), 16000)
    assert rms == 0.0 and len(bands) == N_BANDS and max(bands) == 0.0
    t = np.arange(1280) / 16000
    tone = (0.5 * 32767 * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
    rms, bands = analyze(tone, 16000)
    assert 0.3 < rms < 0.4
    assert max(bands) > 0.5 and bands.index(max(bands)) in range(6, 12)  # 1 kHz lands mid-spectrum
    assert all(0.0 <= b <= 1.0 for b in bands)
    rms2, _ = analyze(tone.astype(np.float32) / 32768.0, 16000)
    assert abs(rms2 - rms) < 1e-3
    assert analyze(np.zeros(0, dtype=np.int16), 16000) == (0.0, [0.0] * N_BANDS)


def test_pcm16_bytes_to_samples_drops_odd_byte():
    assert len(pcm16_bytes_to_samples(b"\x00\x01\x02")) == 1


# ------------------------------------------------------- wake triggers
def test_push_to_talk_honours_talk_event(monkeypatch):
    import daxton.wake.simple as simple

    monkeypatch.setattr(simple.sys, "stdin", type("S", (), {"readline": staticmethod(lambda: threading.Event().wait(5) or "")})())
    stop, talk = threading.Event(), threading.Event()
    talk.set()
    assert PushToTalk().wait(stop, talk) is True and not talk.is_set()
    assert AlwaysListening().wait(stop, None) is True


# ------------------------------------------------- assistant state machine
def make_assistant(settings, bus=None):
    return Assistant(settings, KeywordBrain(), ConsoleSpeaker(), bus=bus)


def test_handle_publishes_transcript_state_and_route(settings):
    bus = EventBus()
    q = bus.subscribe()
    a = make_assistant(settings, bus)
    a.show_tools = False
    reply = a.respond("what time is it")
    assert reply.startswith("It is")
    types = []
    while True:
        try:
            types.append(q.get_nowait()["type"])
        except queue.Empty:
            break
    assert types[:2] == ["user", "state"]  # thinking
    assert "tool" in types and "assistant" in types
    assert types[-1] == "state" and a.state == "idle"
    assert [e["type"] for e in bus.history if e["type"] in ("user", "assistant")] == ["user", "assistant"]


def test_snapshot_shape(settings):
    a = make_assistant(settings)
    snap = a.snapshot()
    assert snap["type"] == "snapshot" and snap["assistant_name"] == "Daxton" and snap["voice_mode"] is False
    assert {"brain", "ears", "voice", "wake", "skills", "history", "thresholds", "started_at"} <= set(snap)
    assert any(k["name"] == "open_app" for k in snap["skills"])


def test_stop_speaking_is_safe_without_audio(settings):
    a = make_assistant(settings)
    a.stop_speaking()  # ConsoleSpeaker inherits Speaker.stop(); nothing to interrupt


# -------------------------------------------------------------- server
fastapi = pytest.importorskip("fastapi")


def make_client(settings):
    from fastapi.testclient import TestClient

    from daxton.ui.server import create_app

    a = make_assistant(settings)
    a.show_tools = False
    return a, TestClient(create_app(a))


def test_dashboard_page_and_state(settings):
    a, client = make_client(settings)
    with client:
        r = client.get("/")
        assert r.status_code == 200 and "<title>Daxton AI</title>" in r.text and "corecanvas" in r.text
        assert client.get("/favicon.ico").headers["content-type"].startswith("image/svg")
        s = client.get("/api/state").json()
        assert s["type"] == "snapshot" and s["brain"] == "keyword"


def test_websocket_roundtrip_and_commands(settings):
    a, client = make_client(settings)

    def read_until(ws, want, limit=30, cmd=None):
        """Collect events until one of type `want` arrives (events from earlier turns may still be queued).

        Bus events and command acks travel different paths, so an earlier command's ack can land after the
        bus event it caused; `cmd` pins an ack to the command that is actually being waited for.
        """
        seen = {}
        for _ in range(limit):
            ev = ws.receive_json()
            if ev["type"] == want and (cmd is None or ev.get("cmd") == cmd):
                seen[ev["type"]] = ev
                return seen
            seen.setdefault(ev["type"], ev)
        raise AssertionError(f"no {want} event; saw {list(seen)}")

    with client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            ws.send_json({"cmd": "say", "text": "remember that the gate code is 4412"})
            seen = read_until(ws, "ack")
            if "assistant" not in seen:
                seen.update(read_until(ws, "assistant"))
            assert seen["assistant"]["text"] == "Noted: the gate code is 4412."
            assert seen["tool"]["name"] == "remember" and seen["ack"]["ok"] is True

            ws.send_json({"cmd": "mute", "value": True})
            seen = read_until(ws, "muted")
            assert seen["muted"]["value"] is True and a.muted is True

            ws.send_json({"cmd": "talk"})
            ack = read_until(ws, "ack", cmd="talk")["ack"]
            assert ack["ok"] is False and "microphone" in ack["error"]

            ws.send_json({"cmd": "run_skill", "name": "current_datetime", "args": {}})
            seen = read_until(ws, "tool")
            assert seen["tool"].get("direct") is True and seen["tool"]["result"].startswith("It is")

            ws.send_json({"cmd": "bogus"})
            ack = read_until(ws, "ack", cmd="bogus")["ack"]
            assert ack["ok"] is False and "unknown command" in ack["error"]

            ws.send_json({"cmd": "pin_tier", "tier": "smart"})
            ack = read_until(ws, "ack", cmd="pin_tier")["ack"]
            assert ack["ok"] is False  # single keyword brain: no tiers to pin


def test_http_command_endpoint(settings):
    a, client = make_client(settings)
    with client:
        r = client.post("/api/command", json={"cmd": "new_conversation"})
        assert r.json()["ok"] is True


def test_telemetry_sample_shape():
    from daxton.ui.server import Telemetry

    t = Telemetry().sample()
    if t.get("available"):
        assert {"cpu", "mem", "net_up", "net_down", "uptime", "host"} <= set(t)
