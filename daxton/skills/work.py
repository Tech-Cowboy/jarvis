"""Real work on the Mac: files, shell, Python, web pages, and handing big jobs to Claude Code.

Everything here acts on the user's own machine at the user's own request, so the guard rails
are about accidents, not intent: paths stay under the home folder and away from secrets,
obviously destructive commands are refused, everything has a timeout, and the shell can
be switched off with DAXTON_SHELL=false.

`delegate_task` runs Claude Code (`claude -p`) headless in a task folder on a background
thread and reports back through the assistant's voice when it finishes, so a
"build me X" request does not hold up the conversation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

from .context import SkillContext
from .registry import skill

HOME = Path.home()
PROTECTED_PARTS = {".ssh", ".gnupg", ".aws", ".config/daxton", "Library/Keychains", ".daxton", ".cloudflared",
                   ".netrc", ".npmrc", ".pypirc", ".docker"}
PROTECTED_NAMES = {".env", "id_rsa", "id_ed25519", "credentials", "token", "secret"}
PROTECTED_WORDS = {"credential", "credentials", "secret", "secrets", "token", "tokens", "api_key", "apikey", "api_keys",
                   "password", "passwords", "passwd", "id_rsa", "id_ed25519", "private_key", "privatekey"}
PROTECTED_SUFFIXES = (".pem", ".p12", ".pfx", ".keychain", ".keychain-db", ".jks")
DENY_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+|-[a-zA-Z]*f[a-zA-Z]*\s+)*(/|~|\$HOME|\.\.)(\s|$|/\*)",  # rm -rf / ~ ..
    r"\bsudo\b", r"\bsu\b", r"\bmkfs\b", r"\bdiskutil\s+(erase|partition|secureErase)", r"\bdd\s+if=",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bkillall\s+(Finder|WindowServer|loginwindow)\b",
    r"\blaunchctl\s+(bootout|unload)\b.*(daxton|cloudflare)", r">\s*/dev/(sd|disk|rdisk)", r"\bchmod\s+-R\s+777\s+/",
    r"\bcurl\b.*\|\s*(ba)?sh\b", r"\bwget\b.*\|\s*(ba)?sh\b", r":\(\)\s*\{\s*:\|:&\s*\};:",
    r"\bcsrutil\b", r"\bspctl\b", r"\bdefaults\s+write\s+com\.apple\.security", r"\bsecurity\s+(delete|set)-",
]
MAX_OUTPUT = 4000


# ------------------------------------------------------------------ guards
def _shell_enabled(ctx: SkillContext | None) -> bool:
    return bool(getattr(getattr(ctx, "settings", None), "shell_enabled", True))


def _safe_path(raw: str, must_exist: bool = False) -> Path:
    """A path under the home folder that is not a secret; raises ValueError otherwise."""
    if not raw or not raw.strip():
        raise ValueError("no path given")
    p = Path(raw.strip()).expanduser()
    if not p.is_absolute():
        p = HOME / p
    p = p.resolve() if p.exists() else Path(os.path.normpath(p.absolute()))  # lexical .. removal for new paths
    try:
        rel = p.relative_to(HOME.resolve())
    except ValueError:
        raise ValueError(f"{p} is outside your home folder; I only work under {HOME}")
    rel_s = str(rel)
    for part in PROTECTED_PARTS:
        if rel_s == part or rel_s.startswith(part + "/") or rel_s.startswith(part + os.sep):
            raise ValueError(f"{rel_s} holds credentials or Daxton's own state; I won't touch it")
    lowered = p.name.lower()
    words = {w for w in re.split(r"[^a-z0-9]+", lowered) if w} | {lowered.rsplit(".", 1)[0]}
    if (p.name in PROTECTED_NAMES or lowered.startswith(".env") or words & PROTECTED_WORDS
            or lowered.endswith(PROTECTED_SUFFIXES)):
        raise ValueError(f"{p.name} looks like a secrets file; I won't touch it")
    if any("secret" in part.lower() or "credential" in part.lower() for part in rel.parts[:-1]):
        raise ValueError(f"{rel_s} is inside a secrets folder; I won't touch it")
    if must_exist and not p.exists():
        raise ValueError(f"{p} does not exist")
    return p


def _denied(command: str) -> str | None:
    for pat in DENY_PATTERNS:
        if re.search(pat, command, re.I):
            return pat
    return None


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f"\n... ({len(text) - limit} more characters)"


# ------------------------------------------------------------------ shell and files
@skill()
def run_command(command: Annotated[str, "A shell command to run on the Mac (bash). Keep it to one line."],
                ctx: SkillContext = None) -> str:
    """Run a shell command on the computer and return its output. Ask before anything destructive."""
    if not _shell_enabled(ctx):
        return "Error: the shell is switched off (DAXTON_SHELL=false)."
    command = (command or "").strip()
    if not command:
        return "Error: empty command."
    if _denied(command):
        return "I won't run that one; it could damage the machine or its settings. Tell me what you are after and I'll find a safer way."
    try:
        r = subprocess.run(["bash", "-lc", command], cwd=str(HOME), capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "That command ran for more than a minute, so I stopped it."
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return f"Exit code {r.returncode}. " + _clip(err or out or "(no output)")
    return _clip(out or "(no output)")


@skill()
def run_python(code: Annotated[str, "Python 3 code to run; print what you want back."], ctx: SkillContext = None) -> str:
    """Run a snippet of Python on the computer and return what it prints."""
    if not _shell_enabled(ctx):
        return "Error: the shell is switched off (DAXTON_SHELL=false)."
    if _denied(code):
        return "I won't run that; it could damage the machine."
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=str(HOME), capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "That took more than a minute, so I stopped it."
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return "Python error: " + _clip(err or out, 1500)
    return _clip(out or "(no output)")


@skill()
def read_file(path: Annotated[str, "File path, absolute or relative to the home folder."],
              max_chars: Annotated[int, "How much of the file to return (default 4000)."] = 4000) -> str:
    """Read a text file on the computer."""
    try:
        p = _safe_path(path, must_exist=True)
        if p.is_dir():
            return list_files(str(p))
        text = p.read_text(encoding="utf-8", errors="replace")
    except ValueError as e:
        return f"Error: {e}"
    except OSError as e:
        return f"Error: could not read {path}: {e}"
    return _clip(text, max(200, int(max_chars)))


@skill()
def write_file(path: Annotated[str, "File path, absolute or relative to the home folder."],
               content: Annotated[str, "The text to write."],
               append: Annotated[bool, "Add to the end instead of replacing the file."] = False) -> str:
    """Write (or append to) a text file on the computer, creating folders as needed."""
    try:
        p = _safe_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a" if append else "w", encoding="utf-8") as f:
            f.write(content or "")
    except ValueError as e:
        return f"Error: {e}"
    except OSError as e:
        return f"Error: could not write {path}: {e}"
    return f"{'Appended to' if append else 'Wrote'} {p.relative_to(HOME.resolve()) if str(p).startswith(str(HOME.resolve())) else p} ({len(content or '')} characters)."


@skill()
def list_files(path: Annotated[str, "Folder path, absolute or relative to the home folder."] = "~") -> str:
    """List what is in a folder on the computer."""
    try:
        p = _safe_path(path, must_exist=True)
    except ValueError as e:
        return f"Error: {e}"
    if not p.is_dir():
        return f"{p} is a file ({p.stat().st_size} bytes)."
    entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    shown = [e.name + ("/" if e.is_dir() else "") for e in entries if not e.name.startswith(".")][:60]
    hidden = sum(1 for e in entries if e.name.startswith("."))
    return f"{p}: " + (", ".join(shown) if shown else "(empty)") + (f" (+{hidden} hidden)" if hidden else "")


# ------------------------------------------------------------------ the web, read properly
def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|aside).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    import html as html_mod

    text = html_mod.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


@skill()
def fetch_page(url: Annotated[str, "The web address to read."],
               max_chars: Annotated[int, "How much text to return (default 4000)."] = 4000) -> str:
    """Read a web page and return its text, for summarising or answering questions about it."""
    import httpx

    url = (url or "").strip()
    if not url:
        return "Error: no address given."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = httpx.get(url, follow_redirects=True, timeout=20,
                      headers={"User-Agent": "DaxtonAI/0.1 (personal assistant; +https://github.com/Tech-Cowboy/daxton-ai)"})
    except Exception as e:
        return f"Error: could not fetch {url}: {e}"
    if r.status_code >= 400:
        return f"Error: {url} answered {r.status_code}."
    ctype = r.headers.get("content-type", "")
    body = r.text if "html" in ctype or "text" in ctype or "json" in ctype else f"(binary content, {ctype})"
    text = _html_to_text(body) if "html" in ctype else body
    return _clip(text, max(500, int(max_chars)))


# ------------------------------------------------------------------ delegating to Claude Code
class _TaskBook:
    def __init__(self) -> None:
        self.tasks: list[dict] = []
        self.lock = threading.Lock()

    def add(self, task: dict) -> None:
        with self.lock:
            self.tasks.append(task)


_book = _TaskBook()


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:n].rstrip("-") or "task"


def find_claude_code(settings=None) -> str | None:
    """The Claude Code executable: CLAUDE_CODE_BIN, PATH, or the usual npm/brew spots."""
    configured = getattr(settings, "claude_code_bin", "") or ""
    for candidate in ([configured] if configured else []) + ["claude"]:
        path = shutil.which(candidate)
        if path:
            return path
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser())
    for candidate in (HOME / ".claude/local/claude", HOME / ".npm-global/bin/claude", Path("/opt/homebrew/bin/claude"),
                      Path("/usr/local/bin/claude")):
        if candidate.is_file():
            return str(candidate)
    return None


def _run_delegated(task: dict, ctx: SkillContext, binary: str, timeout: float) -> None:
    folder = Path(task["folder"])
    started = time.time()
    cmd = [binary, "-p", task["task"], "--output-format", "json", "--permission-mode", "acceptEdits",
           "--max-turns", "60"]
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY",)}  # Claude Code uses its own login
    try:
        r = subprocess.run(cmd, cwd=str(folder), capture_output=True, text=True, timeout=timeout, env=env)
        result_text = ""
        try:
            data = json.loads(r.stdout or "{}")
            result_text = str(data.get("result") or "")
            task["cost_usd"] = data.get("total_cost_usd")
            task["turns"] = data.get("num_turns")
        except json.JSONDecodeError:
            result_text = (r.stdout or "").strip()
        task["status"] = "done" if r.returncode == 0 else "failed"
        task["result"] = result_text or (r.stderr or "").strip()[-2000:] or "(no output)"
    except subprocess.TimeoutExpired:
        task["status"] = "timed out"
        task["result"] = f"stopped after {int(timeout // 60)} minutes"
    except Exception as e:
        task["status"] = "failed"
        task["result"] = str(e)
    task["seconds"] = round(time.time() - started)
    task["finished"] = datetime.now().isoformat(timespec="seconds")
    try:
        log_dir = Path(ctx.settings.data_dir) / "tasks"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{task['id']}.json").write_text(json.dumps(task, indent=2), encoding="utf-8")
    except Exception:
        pass
    summary = task["result"].strip().splitlines()[0][:220] if task["result"].strip() else "no summary"
    bus = getattr(ctx, "bus", None)
    if bus is not None:
        bus.publish("task", id=task["id"], status=task["status"], task=task["task"], result=task["result"][:1500],
                    folder=task["folder"], seconds=task["seconds"])
    ctx.notify(f"Task {task['id']} {task['status']}: {summary}")


@skill()
def delegate_task(task: Annotated[str, "What to build, write, fix or research, in full detail, as you would brief an engineer."],
                  folder: Annotated[str, "Optional folder to work in (a project path); empty for a fresh task folder."] = "",
                  ctx: SkillContext = None) -> str:
    """Hand a big job (code, documents, research, multi-step changes) to Claude Code, which works on the computer in
    the background; the user is told when it is done. Use for anything that takes more than a moment."""
    if not _shell_enabled(ctx):
        return "Error: delegation needs the shell, which is switched off (DAXTON_SHELL=false)."
    settings = ctx.settings
    binary = find_claude_code(settings)
    if not binary:
        return ("Claude Code is not installed on this Mac, so I can't delegate. Install it with "
                "`npm install -g @anthropic-ai/claude-code` and sign in once with `claude`.")
    task = (task or "").strip()
    if len(task) < 8:
        return "Error: tell me what the task is."
    try:
        if folder and folder.strip():
            work = _safe_path(folder)
            work.mkdir(parents=True, exist_ok=True)
        else:
            root = Path(getattr(settings, "tasks_dir", HOME / "Documents/Claude/Projects/daxton-tasks")).expanduser()
            work = root / f"{datetime.now():%Y%m%d-%H%M}-{_slug(task)}"
            work.mkdir(parents=True, exist_ok=True)
            (work / "TASK.md").write_text(f"# Task\n\n{task}\n\nDelegated by Daxton AI on {datetime.now():%Y-%m-%d %H:%M}.\n",
                                          encoding="utf-8")
    except ValueError as e:
        return f"Error: {e}"
    task_id = f"T{len(_book.tasks) + 1}"
    record = {"id": task_id, "task": task, "folder": str(work), "status": "running",
              "started": datetime.now().isoformat(timespec="seconds")}
    _book.add(record)
    timeout = float(getattr(settings, "task_timeout_minutes", 30)) * 60
    threading.Thread(target=_run_delegated, args=(record, ctx, binary, timeout), name=f"daxton-task-{task_id}",
                     daemon=True).start()
    return (f"Started task {task_id} in {work.name}. Claude Code is working on it in the background; "
            "I'll tell you when it's done.")


@skill()
def task_status(task_id: Annotated[str, "A task id like T1, or empty for all recent tasks."] = "") -> str:
    """Check on jobs handed to Claude Code: running, done, or failed, with their results."""
    with _book.lock:
        tasks = list(_book.tasks)
    if task_id:
        tasks = [t for t in tasks if t["id"].lower() == task_id.strip().lower()]
        if not tasks:
            return f"No task called {task_id}."
    if not tasks:
        return "No delegated tasks this session."
    lines = []
    for t in tasks[-6:]:
        line = f"{t['id']} ({t['status']}): {t['task'][:100]}"
        if t.get("result") and t["status"] != "running":
            line += f" Result: {t['result'][:300]}"
        lines.append(line)
    return " ".join(lines)
