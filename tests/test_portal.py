"""The portal: login and sessions, browser voice in, the assistant's voice out."""

from __future__ import annotations

import time

import numpy as np
import pytest

from daxton.assistant import Assistant
from daxton.brain.keyword_brain import KeywordBrain
from daxton.tts import Speaker
from daxton.tts.console import ConsoleSpeaker
from daxton.ui.auth import COOKIE, DashboardAuth, is_local_address, safe_next
from daxton.ui.voice import VoiceCapture

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from daxton.ui.server import create_app  # noqa: E402


# ------------------------------------------------------------------ auth unit
def test_sessions_sign_verify_expire_and_die_with_the_password():
    now = [1_000_000.0]
    auth = DashboardAuth("hunter2", b"secret", session_days=1, clock=lambda: now[0])
    token = auth.issue_session()
    assert auth.verify_session(token)
    assert not auth.verify_session(token[:-1] + ("0" if token[-1] != "0" else "1"))  # tampered signature
    assert not auth.verify_session("")
    assert not auth.verify_session("garbage")
    now[0] += 86400 + 1
    assert not auth.verify_session(token)  # expired
    now[0] -= 86400 + 1
    assert not DashboardAuth("changed", b"secret", clock=lambda: now[0]).verify_session(token)  # new password, old cookie
    assert not DashboardAuth("", b"secret").verify_session(token)  # auth off: no cookie is valid


def test_password_check_throttles_after_repeated_failures():
    now = [0.0]
    auth = DashboardAuth("hunter2", b"secret", clock=lambda: now[0])
    for _ in range(4):
        assert auth.check_password("nope", "1.2.3.4") is False
    assert auth.locked_for("1.2.3.4") == 0
    assert auth.check_password("nope", "1.2.3.4") is False  # fifth failure locks
    assert auth.locked_for("1.2.3.4") > 0
    assert auth.check_password("hunter2", "1.2.3.4") is False  # right password, still locked
    assert auth.check_password("hunter2", "5.6.7.8") is True  # other clients unaffected
    now[0] += 31
    assert auth.check_password("hunter2", "1.2.3.4") is True
    assert auth.locked_for("1.2.3.4") == 0


def test_local_and_origin_rules():
    class Conn:
        def __init__(self, host, headers=None):
            self.client = type("C", (), {"host": host})()
            self.headers = headers or {}
            self.cookies = {}

    auth = DashboardAuth("", b"s", public_hostname="daxton.example.com")
    assert auth.is_local(Conn("127.0.0.1")) and auth.is_local(Conn("::1")) and auth.is_local(Conn("testclient"))
    assert not auth.is_local(Conn("10.0.0.7"))
    assert not auth.is_local(Conn("127.0.0.1", {"cf-connecting-ip": "203.0.113.9"}))  # came through the tunnel
    assert not auth.is_local(Conn("127.0.0.1", {"x-forwarded-for": "203.0.113.9"}))
    assert auth.verdict(Conn("127.0.0.1")) == "ok" and auth.verdict(Conn("10.0.0.7")) == "local_only"
    assert auth.origin_ok(Conn("127.0.0.1", {"host": "localhost:8765", "origin": "http://localhost:8765"}))
    assert auth.origin_ok(Conn("127.0.0.1", {"host": "daxton.example.com", "origin": "https://daxton.example.com"}))
    assert auth.origin_ok(Conn("127.0.0.1", {"host": "localhost:8765"}))  # no Origin: not a browser
    assert not auth.origin_ok(Conn("127.0.0.1", {"host": "localhost:8765", "origin": "https://evil.example"}))
    assert is_local_address(None) and not is_local_address("8.8.8.8") and not is_local_address("nonsense")


def test_safe_next_only_allows_relative_paths():
    assert safe_next("/") == "/" and safe_next("/x?y=1") == "/x?y=1"
    assert safe_next("https://evil.example") == "/" and safe_next("//evil.example") == "/" and safe_next(None) == "/"


# ------------------------------------------------------------ voice capture
def pcm(seconds: float, level: float = 0.0, rate: int = 16000) -> bytes:
    n = int(seconds * rate)
    if level == 0.0:
        return np.zeros(n, dtype="<i2").tobytes()
    t = np.arange(n) / rate
    return (level * 32767 * np.sin(2 * np.pi * 440 * t)).astype("<i2").tobytes()


def test_voice_capture_ends_on_silence_after_speech():
    cap = VoiceCapture(16000, min_speech_rms=0.01, silence_seconds=0.5, max_seconds=15, start_timeout=8)
    for _ in range(5):
        assert cap.feed(pcm(0.08))[0] is None  # leading silence
    for _ in range(5):
        assert cap.feed(pcm(0.08, 0.3))[0] is None  # speech
    reasons = [cap.feed(pcm(0.08))[0] for _ in range(7)]
    assert "silence" in reasons and reasons[-1] is None or reasons[-1] == "silence"
    audio = cap.audio()
    assert audio is not None and audio.dtype == np.float32 and 0.9 < cap.seconds < 1.5
    assert abs(audio).max() <= 1.0


def test_voice_capture_timeout_max_length_and_no_speech():
    cap = VoiceCapture(16000, min_speech_rms=0.01, silence_seconds=0.5, max_seconds=15, start_timeout=0.3)
    assert cap.feed(pcm(0.16))[0] is None
    assert cap.feed(pcm(0.16))[0] == "timeout"
    assert cap.audio() is None
    cap = VoiceCapture(16000, min_speech_rms=0.01, silence_seconds=5, max_seconds=0.5, start_timeout=8)
    assert cap.feed(pcm(0.3, 0.3))[0] is None
    assert cap.feed(pcm(0.3, 0.3))[0] == "max_length"
    assert cap.feed(b"")[0] is None and cap.feed(b"\x01")[0] is None  # empty / odd bytes are ignored


# ----------------------------------------------------------------- server
class FakeTranscriber:
    name = "fake"

    def __init__(self):
        self.calls = []

    def transcribe(self, audio, sample_rate):
        self.calls.append((len(audio), sample_rate))
        return "what time is it"

    def describe(self):
        return "fake ears"


class PcmSpeaker(Speaker):
    """Emits three PCM chunks through on_chunk, like ElevenLabs or a rendered `say` would."""
    name = "pcm"

    def __init__(self):
        self.spoken = []

    def say(self, text):
        self.spoken.append(text)
        if self.on_chunk:
            for i in range(3):
                self.on_chunk(bytes([i]) * 480, 24000)


def make(settings, password="", speaker=None, transcriber=None):
    settings.dashboard_password = password
    settings.dashboard_secret = "unit-test-secret"
    a = Assistant(settings, KeywordBrain(), speaker or ConsoleSpeaker(), transcriber=transcriber)
    a.show_tools = False
    return a, TestClient(create_app(a))


def read_until(ws, want, limit=40, cmd=None):
    """Collect messages until one of type `want` arrives; binary frames are counted under 'bytes'.

    `cmd` pins an ack to one command, since a bus event can overtake the ack of the command that caused it.
    """
    seen: dict = {}
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes") is not None:
            seen["bytes"] = seen.get("bytes", 0) + 1
            if want == "bytes":
                return seen
            continue
        import json
        ev = json.loads(msg["text"])
        if ev["type"] == want and (cmd is None or ev.get("cmd") == cmd):
            seen[ev["type"]] = ev
            return seen
        seen.setdefault(ev["type"], ev)
    raise AssertionError(f"no {want} message; saw {list(seen)}")


def test_without_a_password_only_the_mac_is_served(settings):
    a, client = make(settings)
    with client:
        assert client.get("/").status_code == 200
        assert client.get("/login", follow_redirects=False).status_code == 303  # nothing to log in to
        r = client.get("/", headers={"cf-connecting-ip": "203.0.113.9"})
        assert r.status_code == 403 and "DASHBOARD_PASSWORD" in r.text
        r = client.get("/api/state", headers={"x-forwarded-for": "203.0.113.9"})
        assert r.status_code == 403 and "DASHBOARD_PASSWORD" in r.json()["error"]
        assert client.get("/healthz", headers={"x-forwarded-for": "203.0.113.9"}).json()["ok"] is True
        with client.websocket_connect("/ws", headers={"x-forwarded-for": "203.0.113.9"}) as ws:
            with pytest.raises(Exception):
                ws.receive_json()


def test_login_flow_cookie_and_websocket(settings):
    a, client = make(settings, password="open sesame")
    with client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        assert client.get("/api/state").status_code == 401
        page = client.get("/login?next=/")
        assert page.status_code == 200 and 'name="password"' in page.text and "Daxton AI" in page.text
        bad = client.post("/login", data={"password": "wrong", "next": "/"})
        assert bad.status_code == 401 and "not right" in bad.text and COOKIE not in client.cookies
        ok = client.post("/login", data={"password": "open sesame", "next": "https://evil.example"}, follow_redirects=False)
        assert ok.status_code == 303 and ok.headers["location"] == "/" and COOKIE in client.cookies
        assert client.get("/").status_code == 200
        snap = client.get("/api/state").json()
        assert snap["auth"] is True and snap["remote"] is False
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "snapshot"
        client.get("/logout")
        assert client.get("/api/state").status_code == 401
        # a forged or stale cookie does not get in
        client.cookies.set(COOKIE, "1.2.3")
        assert client.get("/api/state").status_code == 401


def test_websocket_refuses_without_login_and_cross_site_origin(settings):
    a, client = make(settings, password="open sesame")
    with client:
        with client.websocket_connect("/ws") as ws:
            with pytest.raises(Exception):
                ws.receive_json()  # closed with 4401 before anything is sent
        client.post("/login", data={"password": "open sesame"})
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            with pytest.raises(Exception):
                ws.receive_json()
        r = client.post("/api/command", json={"cmd": "new_conversation"}, headers={"origin": "https://evil.example"})
        assert r.status_code == 403


def test_browser_voice_round_trip_and_audio_out(settings):
    ears = FakeTranscriber()
    voice = PcmSpeaker()
    a, client = make(settings, speaker=voice, transcriber=ears)
    with client:
        with client.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
            assert snap["type"] == "snapshot" and snap["remote_voice"] is True and snap["local_audio"] is True
            ws.send_json({"cmd": "audio", "value": True})
            ack = read_until(ws, "ack", cmd="audio")["ack"]
            assert ack["cmd"] == "audio" and ack["value"] is True

            ws.send_json({"cmd": "voice_start", "rate": 16000})
            seen = read_until(ws, "ack", cmd="voice_start")
            assert seen["ack"]["ok"] is True and seen["ack"]["rate"] == 16000
            for _ in range(6):
                ws.send_bytes(pcm(0.08, 0.3))
            ws.send_json({"cmd": "voice_end"})
            seen = read_until(ws, "voice")
            assert seen["voice"]["status"] == "end" and seen["voice"]["reason"] == "released" and seen["voice"]["speech"]
            seen.update(read_until(ws, "assistant"))
            assert ears.calls and ears.calls[0][1] == 16000 and ears.calls[0][0] == 6 * 1280
            assert seen["heard"]["text"] == "what time is it" and "browser" in seen["heard"]["note"]
            assert seen["user"]["text"] == "what time is it" and seen["assistant"]["text"].startswith("It is")
            assert "bytes" not in seen  # the transcript line arrives before its audio, never after
            # the reply was spoken and streamed back as audio
            seen = read_until(ws, "speech_end")
            assert seen["speech_start"]["rate"] == 24000 and seen.get("bytes", 0) == 3
            assert seen["speech_end"]["interrupted"] is False
            assert voice.spoken and voice.spoken[0].startswith("It is")

            ws.send_json({"cmd": "audio", "value": False})
            read_until(ws, "ack", cmd="audio")
            ws.send_json({"cmd": "say", "text": "what time is it"})
            seen = read_until(ws, "ack", cmd="say")
            assert seen.get("bytes", 0) == 0  # audio off: no frames for this client


def test_browser_voice_with_no_speech_and_bad_rate(settings):
    a, client = make(settings, transcriber=FakeTranscriber())
    with client:
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"cmd": "voice_start", "rate": 4000})
            assert read_until(ws, "ack", cmd="voice_start")["ack"]["ok"] is False
            ws.send_json({"cmd": "voice_end"})
            assert read_until(ws, "ack", cmd="voice_end")["ack"]["ok"] is False  # not listening
            ws.send_json({"cmd": "voice_start"})
            read_until(ws, "ack", cmd="voice_start")
            ws.send_bytes(pcm(0.5))  # silence only
            ws.send_json({"cmd": "voice_end"})
            seen = read_until(ws, "heard")
            assert seen["voice"]["speech"] is False and "no speech" in seen["heard"]["note"]
            assert a.state == "idle"


def test_local_audio_toggle_and_silent_player(settings, monkeypatch):
    from daxton.audio import player

    a, client = make(settings)
    with client:
        r = client.post("/api/command", json={"cmd": "local_audio", "value": False})
        assert r.json()["value"] is False and a.local_audio is False and a.speaker.silent is True
        assert client.get("/api/state").json()["local_audio"] is False
    # silent playback never touches sounddevice but still paces the chunks through on_chunk
    monkeypatch.setattr(player, "_output", lambda rate, silent: player._SilentOutput(rate))
    got = []
    t0 = time.time()
    out = player.play_pcm16_bytes(b"\x00" * 9600, 24000, on_chunk=lambda d: got.append(len(d)), silent=True)
    assert out is None and sum(got) == 9600 and 0.15 <= time.time() - t0 < 1.5


def test_remote_turn_strips_the_name_and_builds_ears_lazily(settings, monkeypatch):
    class Ears(FakeTranscriber):
        def transcribe(self, audio, sample_rate):
            return "Daxton, what time is it"

    import daxton.stt as stt

    monkeypatch.setattr(stt, "make_transcriber", lambda s, provider=None: Ears())
    a = Assistant(settings, KeywordBrain(), ConsoleSpeaker())
    a.show_tools = False
    assert a.transcriber is None
    reply = a.remote_turn(np.zeros(1600, dtype=np.float32), 16000)
    assert reply.startswith("It is") and a.transcriber is not None
    assert [e for e in a.bus.history if e["type"] == "user"][0]["text"] == "what time is it"


def test_render_say_pcm_reads_the_wav_it_asked_for(monkeypatch):
    """`say -o file.wav --data-format=LEI16@22050` is stubbed with a WAV writer; the PCM comes back intact."""
    import subprocess
    import wave

    from daxton.tts import macos_say

    def fake_run(cmd, input=None, **kw):
        assert cmd[0] == "say" and cmd[1] == "-o" and cmd[3] == "--data-format=LEI16@22050" and input == "Hello there."
        with wave.open(cmd[2], "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(b"\x01\x00" * 2205)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(macos_say.subprocess, "run", fake_run)
    pcm, rate = macos_say.render_say_pcm("Hello there.")
    assert rate == 22050 and len(pcm) == 4410 and pcm[:2] == b"\x01\x00"


def test_snapshot_carries_the_portal_address_and_qr(settings, tmp_path):
    from daxton import tunnel

    a, client = make(settings, password="open sesame")
    tunnel.quick_log_path(settings).parent.mkdir(parents=True, exist_ok=True)
    tunnel.quick_log_path(settings).write_text("... https://brave-horse.trycloudflare.com |\n")
    with client:
        client.post("/login", data={"password": "open sesame"})
        snap = client.get("/api/state").json()
        assert snap["portal_url"] == "https://brave-horse.trycloudflare.com"
        r = client.get("/portal.svg")
        if snap["portal_qr"]:
            assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg") and "<path" in r.text
        else:
            assert r.status_code == 404
    a2, client2 = make(settings)  # no password: no portal, no QR
    with client2:
        snap = client2.get("/api/state").json()
        assert snap["portal_url"] is None and client2.get("/portal.svg").status_code == 404


def test_voice_watchdog_keeps_the_dashboard_usable_when_the_mic_hangs(settings, monkeypatch):
    """A microphone that never opens (a pending permission prompt) must not hold the dashboard offline."""
    import threading

    from daxton import cli

    a = Assistant(settings, KeywordBrain(), ConsoleSpeaker())
    q = a.bus.subscribe()
    released = threading.Event()

    def stuck_run_voice():
        released.wait(5)  # stands in for Pa_OpenStream waiting on macOS to grant the microphone

    monkeypatch.setattr(a, "run_voice", stuck_run_voice)
    monkeypatch.setattr(cli, "MIC_WAIT_SECONDS", 0.3)
    done = threading.Thread(target=cli._run_voice_with_watchdog, args=(a,), daemon=True)
    done.start()
    time.sleep(0.8)
    assert a.state == "idle"
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert any(e["type"] == "error" and "microphone" in e["text"] for e in events)
    a.stop_event.set()
    released.set()
    done.join(3)
    assert not done.is_alive()
