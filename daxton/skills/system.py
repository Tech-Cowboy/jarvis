"""System skills: time, volume, battery, screenshots, stats, opening paths."""

from __future__ import annotations

import os
import platform
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

from .registry import skill


@skill()
def current_datetime() -> str:
    """Tell the current local date and time."""
    now = datetime.now()
    return f"It is {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %B %d, %Y')}."


@skill()
def set_volume(level: Annotated[int, "Output volume from 0 (mute) to 100."]) -> str:
    """Set the system output volume (macOS)."""
    level = max(0, min(int(level), 100))
    if platform.system() != "Darwin":
        return "Volume control is only implemented for macOS right now."
    r = subprocess.run(["osascript", "-e", f"set volume output volume {level}"], capture_output=True, text=True)
    if r.returncode != 0:
        return f"I couldn't set the volume: {r.stderr.strip()}"
    return "Muted." if level == 0 else f"Volume set to {level} percent."


@skill()
def battery_status() -> str:
    """Report battery charge and whether the machine is plugged in (macOS)."""
    if platform.system() != "Darwin":
        return "Battery status is only implemented for macOS right now."
    r = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True)
    m = re.search(r"(\d+)%;\s*([^;]+);", r.stdout)
    if not m:
        return "I couldn't read the battery status. This may be a desktop Mac."
    pct, state = m.group(1), m.group(2).strip()
    src = "on AC power" if "AC Power" in r.stdout else "on battery"
    return f"Battery is at {pct} percent, {state}, {src}."


@skill()
def take_screenshot() -> str:
    """Take a screenshot of the whole screen and save it to the Desktop."""
    desktop = Path.home() / "Desktop"
    desktop.mkdir(exist_ok=True)
    path = desktop / f"daxton-{datetime.now():%Y%m%d-%H%M%S}.png"
    system = platform.system()
    if system == "Darwin":
        r = subprocess.run(["screencapture", "-x", str(path)], capture_output=True, text=True)
        if r.returncode != 0:
            return f"Screenshot failed: {r.stderr.strip() or 'check Screen Recording permission'}"
        return f"Screenshot saved to the Desktop as {path.name}."
    return "Screenshots are only implemented for macOS right now."


@skill()
def system_stats() -> str:
    """Report CPU count, load, memory and uptime for this computer."""
    cpu = os.cpu_count() or 0
    try:
        load1, load5, _ = os.getloadavg()
        load = f"load {load1:.1f} (1 min), {load5:.1f} (5 min)"
    except (AttributeError, OSError):
        load = "load unavailable"
    mem = ""
    if platform.system() == "Darwin":
        r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip().isdigit():
            mem = f", {int(r.stdout) / 1e9:.0f} gigabytes of memory"
    try:
        boot = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
        secs = int(re.search(r"sec = (\d+)", boot).group(1))  # type: ignore[union-attr]
        up = time.time() - secs
        uptime = f", up {int(up // 3600)} hours {int((up % 3600) // 60)} minutes"
    except Exception:
        uptime = ""
    return f"{platform.system()} {platform.machine()}, {cpu} CPU cores, {load}{mem}{uptime}."


@skill()
def open_path(path: Annotated[str, "A file or folder path to open with its default app, e.g. '~/Downloads'."]) -> str:
    """Open a file or folder on this computer with its default application."""
    p = Path(path).expanduser()
    if not p.exists():
        return f"There is nothing at {path}."
    system = platform.system()
    if system == "Darwin":
        r = subprocess.run(["open", str(p)], capture_output=True, text=True)
    elif system == "Windows":
        os.startfile(str(p))  # type: ignore[attr-defined]
        return f"Opened {p.name}."
    else:
        r = subprocess.run(["xdg-open", str(p)], capture_output=True, text=True)
    return f"Opened {p.name}." if r.returncode == 0 else f"I couldn't open {p.name}: {r.stderr.strip()}"


@skill()
def sleep_display() -> str:
    """Put the display to sleep (macOS)."""
    if platform.system() != "Darwin":
        return "Display sleep is only implemented for macOS right now."
    subprocess.run(["pmset", "displaysleepnow"], capture_output=True)
    return "Display going to sleep."
