"""Application launching and quitting (macOS first; Linux and Windows best-effort)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

from .registry import skill

# Spoken name -> real application name. Keys are lower-case.
ALIASES: dict[str, str] = {
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "browser": "Safari",
    "vscode": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "code": "Visual Studio Code",
    "visual studio": "Visual Studio Code",
    "terminal": "Terminal",
    "iterm": "iTerm",
    "finder": "Finder",
    "settings": "System Settings",
    "system preferences": "System Settings",
    "preferences": "System Settings",
    "calculator": "Calculator",
    "calendar": "Calendar",
    "mail": "Mail",
    "email": "Mail",
    "messages": "Messages",
    "imessage": "Messages",
    "facetime": "FaceTime",
    "notes": "Notes",
    "reminders": "Reminders",
    "music": "Music",
    "apple music": "Music",
    "photos": "Photos",
    "preview": "Preview",
    "text edit": "TextEdit",
    "textedit": "TextEdit",
    "activity monitor": "Activity Monitor",
    "app store": "App Store",
    "maps": "Maps",
    "spotify": "Spotify",
    "slack": "Slack",
    "discord": "Discord",
    "zoom": "zoom.us",
    "obsidian": "Obsidian",
    "cursor": "Cursor",
    "claude": "Claude",
    "chatgpt": "ChatGPT",
    "notion": "Notion",
    "excel": "Microsoft Excel",
    "word": "Microsoft Word",
    "powerpoint": "Microsoft PowerPoint",
    "outlook": "Microsoft Outlook",
    "teams": "Microsoft Teams",
    "firefox": "Firefox",
    "safari": "Safari",
    "arc": "Arc",
    "brave": "Brave Browser",
    "docker": "Docker",
    "postman": "Postman",
    "figma": "Figma",
    "canva": "Canva",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "1password": "1Password",
    "quicktime": "QuickTime Player",
    "keynote": "Keynote",
    "pages": "Pages",
    "numbers": "Numbers",
    "xcode": "Xcode",
}

APP_DIRS = ["/Applications", "/System/Applications", "/System/Applications/Utilities", str(Path.home() / "Applications")]


def _clean(name: str) -> str:
    name = name.strip().strip("'\"").rstrip(".!?")
    for suffix in (" app", " application", " for me", " please"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    for prefix in ("the ", "my "):
        if name.lower().startswith(prefix):
            name = name[len(prefix):]
    return name.strip()


def resolve_app_name(name: str) -> str:
    """Map a spoken app name to the app's real name (aliases, then installed apps, then as-is)."""
    clean = _clean(name)
    low = clean.lower()
    if low in ALIASES:
        return ALIASES[low]
    matches = find_installed_apps(clean, limit=1)
    if matches:
        return matches[0]
    return clean


def find_installed_apps(query: str, limit: int = 10) -> list[str]:
    """Case-insensitive substring match against installed macOS apps (empty on other platforms)."""
    if platform.system() != "Darwin":
        return []
    q = query.lower().strip()
    found: list[str] = []
    for d in APP_DIRS:
        p = Path(d)
        if not p.is_dir():
            continue
        try:
            for entry in sorted(p.iterdir()):
                if entry.suffix == ".app" and q in entry.stem.lower() and entry.stem not in found:
                    found.append(entry.stem)
        except PermissionError:
            continue
    # exact-ish matches first
    found.sort(key=lambda n: (n.lower() != q, not n.lower().startswith(q), len(n)))
    return found[:limit]


@skill()
def open_app(name: Annotated[str, "Application name as the user said it, e.g. 'Safari', 'Spotify', 'VS Code'."]) -> str:
    """Open (launch or bring to front) an application on this computer."""
    app = resolve_app_name(name)
    system = platform.system()
    if system == "Darwin":
        r = subprocess.run(["open", "-a", app], capture_output=True, text=True)
        if r.returncode == 0:
            return f"Opened {app}."
        suggestions = find_installed_apps(_clean(name), limit=3)
        if suggestions:
            return f"I couldn't open '{app}'. Did you mean: {', '.join(suggestions)}?"
        return f"I couldn't find an application called '{app}'."
    if system == "Windows":
        try:
            os.startfile(app)  # type: ignore[attr-defined]
            return f"Opened {app}."
        except OSError as e:
            return f"I couldn't open '{app}': {e}"
    # Linux: try the command name, then xdg-open
    exe = shutil.which(app) or shutil.which(app.lower().replace(" ", "-"))
    if exe:
        subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Opened {app}."
    return f"I couldn't find an application called '{app}'."


@skill()
def quit_app(name: Annotated[str, "Application name to quit, e.g. 'Spotify'."]) -> str:
    """Quit (close) a running application gracefully."""
    app = resolve_app_name(name)
    system = platform.system()
    if system == "Darwin":
        script = f'tell application "{app}" to quit'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return f"Quit {app}."
        return f"I couldn't quit {app}: {r.stderr.strip() or 'unknown error'}"
    if system == "Windows":
        r = subprocess.run(["taskkill", "/IM", f"{app}.exe", "/F"], capture_output=True, text=True)
        return f"Quit {app}." if r.returncode == 0 else f"I couldn't quit {app}."
    r = subprocess.run(["pkill", "-f", "-i", app], capture_output=True, text=True)
    return f"Quit {app}." if r.returncode == 0 else f"I couldn't find a running process for {app}."


@skill()
def find_app(query: Annotated[str, "Partial application name to look for, e.g. 'photo'."]) -> str:
    """List installed applications whose name contains the query. Use when unsure an app exists."""
    matches = find_installed_apps(query, limit=10)
    if not matches:
        return f"No installed application matches '{query}'."
    return "Installed apps matching: " + ", ".join(matches) + "."


@skill()
def running_apps() -> str:
    """List the applications currently running (macOS)."""
    if platform.system() != "Darwin":
        return "Listing running apps is only supported on macOS right now."
    script = 'tell application "System Events" to get name of every process whose background only is false'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return f"I couldn't list running apps: {r.stderr.strip()}"
    names = [n.strip() for n in r.stdout.split(",") if n.strip()]
    return "Running: " + ", ".join(names) + "."
