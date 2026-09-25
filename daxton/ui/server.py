"""The dashboard server: serves the HUD page, streams assistant events over a WebSocket, takes commands.

    GET  /            the dashboard (daxton/ui/static/index.html)
    GET  /login       the login page (only when DASHBOARD_PASSWORD is set); POST /login signs in
    GET  /logout      ends the session
    GET  /healthz     {"ok": true} for tunnel and uptime checks
    GET  /api/state   snapshot: state, configuration, skills, recent history
    POST /api/command one command, same shape as over the WebSocket
    WS   /ws          events out (state, user, assistant, tool, route, audio, telemetry, ...)
                      commands in ({"cmd": "say", "text": ...}, {"cmd": "talk"}, ...)
                      voice in: {"cmd": "voice_start", "rate": 16000}, binary PCM16 frames, {"cmd": "voice_end"}
                      voice out: {"cmd": "audio", "value": true} then {"type": "speech_start"}, binary PCM16
                      frames, {"type": "speech_end"} for every utterance the assistant speaks

Runs uvicorn in a daemon thread so the voice loop keeps the main thread (audio
callbacks want it). Everything that touches the assistant runs in worker
threads; the event loop only shuffles JSON and audio frames.

Access control lives in auth.py: without DASHBOARD_PASSWORD only the Mac itself is
served; with it, every page, API call and socket needs the session cookie.
"""

from __future__ import annotations

import asyncio
import html
import json
from contextlib import asynccontextmanager
import logging
import platform
import queue
import socket
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from .auth import COOKIE, PUBLIC_PATHS, DashboardAuth, load_or_create_secret, safe_next
from .voice import MAX_FRAME_BYTES, SpeechRelay, VoiceCapture

log = logging.getLogger(__name__)

# FastAPI resolves string annotations against this module's globals (we use `from __future__ import annotations`),
# so the framework names must live here, not inside create_app().
try:
    from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
except ImportError:  # the [ui] extra is optional
    FastAPI = Request = WebSocket = WebSocketDisconnect = None  # type: ignore
    HTMLResponse = JSONResponse = RedirectResponse = Response = None  # type: ignore

STATIC_DIR = Path(__file__).parent / "static"
COMMANDS = ("say", "talk", "stop_speaking", "pin_tier", "mute", "local_audio", "new_conversation", "run_skill",
            "end_session")
VOICE_COMMANDS = ("voice_start", "voice_end", "audio")
OUTBOX_LIMIT = 600  # queued messages per connection before audio frames are dropped for that client

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",  # no-referrer would make browsers send Origin: null, breaking the CSRF check
    "Permissions-Policy": "microphone=(self), camera=()",
    "Cache-Control": "no-store",
}


# ------------------------------------------------------------------ telemetry
class Telemetry:
    """System numbers via psutil, sampled once a second."""

    def __init__(self) -> None:
        self._last_net: tuple[float, int, int] | None = None
        try:
            import psutil
            psutil.cpu_percent(interval=None)  # prime the counter
        except Exception:
            pass

    def sample(self) -> dict[str, Any]:
        try:
            import psutil
        except ImportError:
            return {"available": False}
        now = time.time()
        vm = psutil.virtual_memory()
        try:
            disk = psutil.disk_usage("/").percent
        except Exception:
            disk = None
        net = psutil.net_io_counters()
        up = down = 0.0
        if self._last_net is not None:
            t0, sent0, recv0 = self._last_net
            dt = max(now - t0, 1e-3)
            up = (net.bytes_sent - sent0) / dt
            down = (net.bytes_recv - recv0) / dt
        self._last_net = (now, net.bytes_sent, net.bytes_recv)
        battery = None
        try:
            b = psutil.sensors_battery()
            if b is not None:
                battery = {"percent": round(b.percent), "plugged": bool(b.power_plugged),
                           "minutes_left": (None if b.secsleft in (None, -1, -2) or b.secsleft < 0 else b.secsleft // 60)}
        except Exception:
            pass
        try:
            load1, load5, load15 = psutil.getloadavg()
        except Exception:
            load1 = load5 = load15 = 0.0
        return {
            "available": True,
            "cpu": psutil.cpu_percent(interval=None),
            "cpu_count": psutil.cpu_count() or 0,
            "load": [round(load1, 2), round(load5, 2), round(load15, 2)],
            "mem": vm.percent,
            "mem_used_gb": round(vm.used / 1e9, 1),
            "mem_total_gb": round(vm.total / 1e9, 1),
            "disk": disk,
            "net_up": round(up),
            "net_down": round(down),
            "battery": battery,
            "uptime": round(now - psutil.boot_time()),
            "processes": len(psutil.pids()),
            "host": socket.gethostname(),
            "os": f"{platform.system()} {platform.release()}",
        }


# ------------------------------------------------------------------ commands
def handle_command(assistant, msg: dict[str, Any]) -> dict[str, Any] | None:
    """Run one dashboard command against the assistant (blocking; called in a worker thread)."""
    cmd = str(msg.get("cmd", "")).lower()
    if cmd == "say":
        text = str(msg.get("text", "")).strip()
        if not text:
            return {"type": "ack", "cmd": cmd, "ok": False, "error": "empty request"}
        speak = msg.get("speak")
        if speak is None:  # default: aloud in voice mode, silent in text-only mode
            speak = assistant.mic is not None or assistant.audio_listeners > 0
        if speak:
            assistant.respond(text)
        else:
            assistant.handle(text)
            assistant._set_state("idle")
        return {"type": "ack", "cmd": cmd, "ok": True}
    if cmd == "talk":
        if assistant.mic is None:
            return {"type": "ack", "cmd": cmd, "ok": False, "error": "no microphone in this session"}
        assistant.talk_event.set()
        return {"type": "ack", "cmd": cmd, "ok": True}
    if cmd == "stop_speaking":
        assistant.stop_speaking()
        return {"type": "ack", "cmd": cmd, "ok": True}
    if cmd == "pin_tier":
        tier = msg.get("tier")
        if not hasattr(assistant.llm, "pinned"):
            return {"type": "ack", "cmd": cmd, "ok": False, "error": "tiered routing is off"}
        assistant.llm.pinned = tier if tier in ("free", "fast", "smart") else None
        assistant.bus.publish("pinned", tier=assistant.llm.pinned)
        return {"type": "ack", "cmd": cmd, "ok": True, "tier": assistant.llm.pinned}
    if cmd == "mute":
        assistant.muted = bool(msg.get("value", not assistant.muted))
        assistant.bus.publish("muted", value=assistant.muted)
        return {"type": "ack", "cmd": cmd, "ok": True, "value": assistant.muted}
    if cmd == "local_audio":
        assistant.local_audio = bool(msg.get("value", not assistant.local_audio))
        return {"type": "ack", "cmd": cmd, "ok": True, "value": assistant.local_audio}
    if cmd == "new_conversation":
        assistant.router.reset()
        assistant.bus.publish("notice", text="conversation cleared")
        return {"type": "ack", "cmd": cmd, "ok": True}
    if cmd == "run_skill":
        name = str(msg.get("name", ""))
        args = msg.get("args") or {}
        result = assistant.registry.run(name, args, assistant.ctx)
        assistant.bus.publish("tool", name=name, arguments=args, result=result[:500], direct=True)
        return {"type": "ack", "cmd": cmd, "ok": not result.startswith("Error"), "result": result}
    if cmd == "end_session":
        assistant.stop_event.set()
        assistant.bus.publish("notice", text="session ending")
        return {"type": "ack", "cmd": cmd, "ok": True}
    return {"type": "ack", "cmd": cmd, "ok": False, "error": f"unknown command; try {', '.join(COMMANDS)}"}


# ----------------------------------------------------------------------- app
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><g fill="none" stroke="#3fd0ff" stroke-width="2">'
           '<circle cx="20" cy="20" r="17" opacity=".6"/><circle cx="20" cy="20" r="10" stroke-dasharray="6 4"/>'
           '<circle cx="20" cy="20" r="4" fill="#3fd0ff"/></g></svg>')

LOCAL_ONLY_TEXT = ("This dashboard is reachable from the Mac it runs on only. To use it from elsewhere, set "
                   "DASHBOARD_PASSWORD in .env and restart it (see docs/portal.md).")


def make_auth(settings) -> DashboardAuth:
    secret = settings.dashboard_secret.encode() if settings.dashboard_secret else \
        load_or_create_secret(Path(settings.data_dir) / "dashboard.secret")
    return DashboardAuth(password=settings.dashboard_password, secret=secret,
                         session_days=settings.dashboard_session_days, public_hostname=settings.public_hostname)


def render_login(product: str, error: str = "", next_path: str = "/", locked: float = 0.0) -> str:
    page = (STATIC_DIR / "login.html").read_text(encoding="utf-8")
    message = error
    if locked > 0:
        message = f"Too many attempts. Try again in {int(locked) + 1} s."
    return (page.replace("{{product}}", html.escape(product))
                .replace("{{error}}", html.escape(message))
                .replace("{{next}}", html.escape(safe_next(next_path))))


def create_app(assistant, auth: DashboardAuth | None = None):
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("The dashboard needs fastapi and uvicorn: pip install 'daxton-ai[ui]'")

    settings = assistant.settings
    auth = auth or make_auth(settings)
    product = getattr(settings, "product_name", "Daxton AI")
    telemetry = Telemetry()
    clients: set[WebSocket] = set()

    async def telemetry_loop() -> None:
        while True:
            try:
                if clients:
                    sample = await asyncio.to_thread(telemetry.sample)
                    sample["session_uptime"] = round(time.time() - assistant.started_at)
                    sample["stats"] = dict(getattr(assistant.llm, "stats", {}) or {})
                    sample["timers"] = _timers()
                    assistant.bus.publish("telemetry", **sample)
            except Exception as e:  # keep sampling whatever happens
                log.debug("telemetry: %s", e)
            await asyncio.sleep(1.0)

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(telemetry_loop())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title=f"{product} dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.assistant = assistant
    app.state.clients = clients
    app.state.auth = auth

    def _snapshot(conn) -> dict[str, Any]:
        snap = assistant.snapshot()
        snap["auth"] = auth.enabled
        snap["remote"] = not auth.is_local(conn)
        return snap

    # ------------------------------------------------------------ access
    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path not in PUBLIC_PATHS:
            verdict = auth.verdict(request)
            if verdict == "local_only":
                if path.startswith("/api/"):
                    return _secure(JSONResponse({"error": LOCAL_ONLY_TEXT}, status_code=403))
                return _secure(HTMLResponse(_plain_page(product, "Local only", LOCAL_ONLY_TEXT), status_code=403))
            if verdict == "login":
                if path.startswith("/api/"):
                    return _secure(JSONResponse({"error": "login required", "login": "/login"}, status_code=401))
                return _secure(RedirectResponse(f"/login?next={quote(path, safe='/')}", status_code=303))
            if request.method == "POST" and not auth.origin_ok(request):
                return _secure(JSONResponse({"error": "cross-site request refused"}, status_code=403))
        response = await call_next(request)
        return _secure(response)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        if not auth.enabled:
            return RedirectResponse("/", status_code=303) if auth.is_local(request) else \
                HTMLResponse(_plain_page(product, "Local only", LOCAL_ONLY_TEXT), status_code=403)
        if auth.verify_session(request.cookies.get(COOKIE, "")):
            return RedirectResponse(safe_next(request.query_params.get("next")), status_code=303)
        return HTMLResponse(render_login(product, next_path=request.query_params.get("next", "/"),
                                         locked=auth.locked_for(_client_ip(request))))

    @app.post("/login")
    async def login_submit(request: Request) -> Response:
        if not auth.enabled:
            return HTMLResponse(_plain_page(product, "Local only", LOCAL_ONLY_TEXT), status_code=403)
        if not auth.origin_ok(request):
            return HTMLResponse(_plain_page(product, "Refused", "cross-site login refused"), status_code=403)
        form = parse_qs((await request.body()).decode("utf-8", "replace"), keep_blank_values=True)
        password = (form.get("password") or [""])[0]
        next_path = safe_next((form.get("next") or ["/"])[0])
        ip = _client_ip(request)
        if not auth.check_password(password, ip):
            await asyncio.sleep(0.4)
            locked = auth.locked_for(ip)
            body = render_login(product, error="That password is not right.", next_path=next_path, locked=locked)
            return HTMLResponse(body, status_code=429 if locked > 0 else 401)
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(COOKIE, auth.issue_session(), max_age=auth.cookie_max_age(), httponly=True,
                            samesite="lax", secure=(request.url.scheme == "https"), path="/")
        assistant.bus.publish("notice", text=f"dashboard login from {ip}")
        return response

    @app.get("/logout")
    async def logout(request: Request) -> Response:
        response = RedirectResponse("/login" if auth.enabled else "/", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "product": product, "state": assistant.state})

    # ------------------------------------------------------------- pages
    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(FAVICON, media_type="image/svg+xml")

    @app.get("/api/state")
    async def state(request: Request) -> JSONResponse:
        return JSONResponse(_snapshot(request))

    @app.post("/api/command")
    async def command(msg: dict[str, Any]) -> JSONResponse:
        result = await asyncio.to_thread(handle_command, assistant, msg)
        return JSONResponse(result or {"type": "ack", "ok": True})

    # ------------------------------------------------------------ socket
    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        verdict = auth.verdict(websocket)
        if verdict != "ok" or not auth.origin_ok(websocket):
            await websocket.accept()
            code, reason = (4401, "login required") if verdict == "login" else (4403, "refused")
            await websocket.close(code=code, reason=reason)
            return
        await websocket.accept()
        clients.add(websocket)
        loop = asyncio.get_running_loop()
        outbox: asyncio.Queue = asyncio.Queue()
        events = assistant.bus.subscribe()

        def put(item: Any) -> None:
            if isinstance(item, bytes) and outbox.qsize() > OUTBOX_LIMIT:
                return  # a stalled page loses audio, never control messages
            outbox.put_nowait(item)

        relay = SpeechRelay(put)
        put(_snapshot(websocket))

        async def pump() -> None:
            while True:
                try:  # short timeouts keep the executor thread cancellable
                    event = await loop.run_in_executor(None, events.get, True, 0.5)
                except queue.Empty:
                    continue
                if not relay.handle(event):
                    put(event)

        async def sender() -> None:
            while True:
                item = await outbox.get()
                if isinstance(item, bytes):
                    await websocket.send_bytes(item)
                else:
                    await websocket.send_text(json.dumps(item, default=str))

        session = _VoiceSession(assistant, put, relay)
        tasks = [asyncio.create_task(pump()), asyncio.create_task(sender())]
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is not None:
                    session.feed(data)
                    continue
                raw = msg.get("text")
                if raw is None:
                    continue
                try:
                    cmd = json.loads(raw)
                except json.JSONDecodeError:
                    put({"type": "ack", "ok": False, "error": "bad json"})
                    continue
                if not isinstance(cmd, dict):
                    put({"type": "ack", "ok": False, "error": "expected an object"})
                    continue
                name = str(cmd.get("cmd", "")).lower()
                if name in VOICE_COMMANDS:
                    put(session.command(name, cmd))
                    continue
                result = await asyncio.to_thread(handle_command, assistant, cmd)
                if result is not None:
                    put(result)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug("websocket closed: %s", e)
        finally:
            for t in tasks:
                t.cancel()
            assistant.bus.unsubscribe(events)
            clients.discard(websocket)
            session.close()

    return app


class _VoiceSession:
    """The browser-microphone state for one dashboard connection."""

    def __init__(self, assistant, put, relay: SpeechRelay):
        self.assistant = assistant
        self.put = put
        self.relay = relay
        self.capture: VoiceCapture | None = None
        self.turn: threading.Thread | None = None
        self.frames = 0

    def command(self, name: str, cmd: dict[str, Any]) -> dict[str, Any]:
        s = self.assistant.settings
        if name == "audio":
            want = bool(cmd.get("value", True))
            if want != self.relay.enabled:
                self.relay.enabled = want
                self.assistant.audio_listeners += 1 if want else -1
            return {"type": "ack", "cmd": name, "ok": True, "value": want}
        if name == "voice_start":
            if self.turn is not None and self.turn.is_alive():
                return {"type": "ack", "cmd": name, "ok": False, "error": "still answering the last one"}
            rate = int(cmd.get("rate", 16000) or 16000)
            if not 8000 <= rate <= 48000:
                return {"type": "ack", "cmd": name, "ok": False, "error": "sample rate must be 8000..48000"}
            self.capture = VoiceCapture(rate, s.min_speech_rms, s.silence_seconds, s.max_utterance_seconds,
                                        start_timeout=60.0)
            self.frames = 0
            self.assistant.bus.publish("wake", mode="browser")
            self.assistant._set_state("listening")
            return {"type": "ack", "cmd": name, "ok": True, "rate": rate}
        if name == "voice_end":
            if self.capture is None:
                return {"type": "ack", "cmd": name, "ok": False, "error": "not listening"}
            self.finish("released")
            return {"type": "ack", "cmd": name, "ok": True}
        return {"type": "ack", "cmd": name, "ok": False, "error": "unknown voice command"}

    def feed(self, data: bytes) -> None:
        cap = self.capture
        if cap is None or len(data) > MAX_FRAME_BYTES:
            return
        reason, _level = cap.feed(data)
        self.frames += 1
        if self.frames % 2 == 0:  # ~6 Hz of level meter is plenty
            import numpy as np

            usable = len(data) - (len(data) % 2)
            self.assistant.on_remote_frame(np.frombuffer(data[:usable], dtype="<i2"), cap.sample_rate)
        if reason:
            self.finish(reason)

    def finish(self, reason: str) -> None:
        cap, self.capture = self.capture, None
        if cap is None:
            return
        audio = cap.audio()
        self.put({"type": "voice", "status": "end", "reason": reason, "seconds": round(cap.seconds, 2),
                  "speech": audio is not None})
        if audio is None:
            self.assistant.bus.publish("heard", text="", note=f"browser mic: no speech ({reason})")
            self.assistant._set_state("idle")
            return
        self.turn = threading.Thread(target=self._run, args=(audio, cap.sample_rate), name="daxton-remote-turn",
                                     daemon=True)
        self.turn.start()

    def _run(self, audio, rate: int) -> None:
        try:
            self.assistant.remote_turn(audio, rate)
        except Exception as e:
            log.error("remote turn failed: %s", e)
            self.assistant.bus.publish("error", text=f"remote turn failed: {e}")
            self.assistant._set_state("idle")

    def close(self) -> None:
        self.capture = None
        if self.relay.enabled:
            self.relay.enabled = False
            self.assistant.audio_listeners = max(0, self.assistant.audio_listeners - 1)


def _client_ip(request) -> str:
    client = getattr(request, "client", None)
    return getattr(client, "host", "") or ""


def _secure(response):
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


def _plain_page(product: str, title: str, text: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(product)}</title>"
            "<style>body{background:#04080d;color:#cfe9f5;font-family:Helvetica,Arial,sans-serif;display:flex;"
            "align-items:center;justify-content:center;height:100vh;margin:0}div{max-width:460px;padding:28px;"
            "border:1px solid rgba(80,200,255,.3)}h1{font-weight:300;letter-spacing:.3em;color:#a6ecff;font-size:18px}"
            "</style></head><body><div>"
            f"<h1>{html.escape(title.upper())}</h1><p>{html.escape(text)}</p></div></body></html>")


def _timers() -> list[dict[str, Any]]:
    try:
        from ..skills.timers import _book
    except Exception:
        return []
    out = []
    for t in _book.timers:
        if t.done:
            continue
        out.append({"label": t.label, "remaining": max(0, int(t.seconds - (time.time() - t.started))), "total": t.seconds})
    return out


def run_server(assistant, host: str = "127.0.0.1", port: int = 8765) -> threading.Thread:
    """Start uvicorn on a daemon thread and return it once the socket is listening (or after 5 s)."""
    import uvicorn

    app = create_app(assistant)
    # proxy headers from cloudflared (loopback) give the real visitor address and https scheme
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False,
                            proxy_headers=True, forwarded_allow_ips="127.0.0.1,::1", ws_max_size=1_000_000)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="daxton-ui", daemon=True)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not getattr(server, "started", False):
        time.sleep(0.05)
    if not thread.is_alive():
        raise RuntimeError(f"dashboard server could not start on {host}:{port} (port in use?)")
    return thread


def open_dashboard(url: str, app_window: bool = False) -> str:
    """Open the dashboard in a browser; with app_window, try Chrome's chromeless app mode first (macOS)."""
    import shutil
    import subprocess
    import webbrowser

    if app_window and platform.system() == "Darwin":
        for name in ("Google Chrome", "Chromium", "Brave Browser", "Microsoft Edge"):
            r = subprocess.run(["open", "-na", name, "--args", f"--app={url}", "--window-size=1440,900"],
                               capture_output=True)
            if r.returncode == 0:
                return f"{name} app window"
    elif app_window:
        for exe in ("google-chrome", "chromium", "chromium-browser", "brave-browser"):
            path = shutil.which(exe)
            if path:
                subprocess.Popen([path, f"--app={url}", "--window-size=1440,900"])
                return f"{exe} app window"
    webbrowser.open(url)
    return "default browser"
