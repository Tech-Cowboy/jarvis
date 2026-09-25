"""The dashboard server: serves the HUD page, streams assistant events over a WebSocket, takes commands.

    GET  /            the dashboard (jarvis/ui/static/index.html)
    GET  /api/state   snapshot: state, configuration, skills, recent history
    WS   /ws          events out (state, user, assistant, tool, route, audio, telemetry, ...)
                      commands in ({"cmd": "say", "text": ...}, {"cmd": "talk"}, ...)

Runs uvicorn in a daemon thread so the voice loop keeps the main thread (audio
callbacks want it). Everything that touches the assistant runs in worker
threads; the event loop only shuffles JSON.
"""

from __future__ import annotations

import asyncio
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

log = logging.getLogger(__name__)

# FastAPI resolves string annotations against this module's globals (we use `from __future__ import annotations`),
# so the framework names must live here, not inside create_app().
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse, Response
except ImportError:  # the [ui] extra is optional
    FastAPI = WebSocket = WebSocketDisconnect = HTMLResponse = JSONResponse = Response = None  # type: ignore

STATIC_DIR = Path(__file__).parent / "static"
COMMANDS = ("say", "talk", "stop_speaking", "pin_tier", "mute", "new_conversation", "run_skill", "end_session")


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
            speak = assistant.mic is not None
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


def create_app(assistant):
    if FastAPI is None:  # pragma: no cover
        raise RuntimeError("The dashboard needs fastapi and uvicorn: pip install 'jarvis-voice-assistant[ui]'")

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

    app = FastAPI(title="JARVIS dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.assistant = assistant
    app.state.clients = clients

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(FAVICON, media_type="image/svg+xml")

    @app.get("/api/state")
    async def state() -> JSONResponse:
        return JSONResponse(assistant.snapshot())

    @app.post("/api/command")
    async def command(msg: dict[str, Any]) -> JSONResponse:
        result = await asyncio.to_thread(handle_command, assistant, msg)
        return JSONResponse(result or {"type": "ack", "ok": True})

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        clients.add(websocket)
        q = assistant.bus.subscribe()
        loop = asyncio.get_running_loop()
        await websocket.send_text(json.dumps(assistant.snapshot(), default=str))

        async def pump() -> None:
            while True:
                try:  # short timeouts keep the executor thread cancellable
                    event = await loop.run_in_executor(None, q.get, True, 0.5)
                except queue.Empty:
                    continue
                await websocket.send_text(json.dumps(event, default=str))

        sender = asyncio.create_task(pump())
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_text(json.dumps({"type": "ack", "ok": False, "error": "bad json"}))
                    continue
                result = await asyncio.to_thread(handle_command, assistant, msg)
                if result is not None:
                    await websocket.send_text(json.dumps(result, default=str))
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug("websocket closed: %s", e)
        finally:
            sender.cancel()
            assistant.bus.unsubscribe(q)
            clients.discard(websocket)

    return app


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
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="jarvis-ui", daemon=True)
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
