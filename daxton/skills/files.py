"""Local file search (Spotlight on macOS, `find` elsewhere)."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Annotated

from .registry import skill

SEARCH_ROOTS = ["Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music"]


def _find_with_spotlight(query: str, limit: int) -> list[str]:
    r = subprocess.run(["mdfind", "-name", query], capture_output=True, text=True, timeout=20)
    paths = [p for p in r.stdout.splitlines() if p.strip()]
    home = str(Path.home())
    # Prefer files under the home folder, then the rest
    paths.sort(key=lambda p: (not p.startswith(home), len(p)))
    return paths[:limit]


def _find_with_find(query: str, limit: int) -> list[str]:
    roots = [str(Path.home() / r) for r in SEARCH_ROOTS if (Path.home() / r).is_dir()] or [str(Path.home())]
    cmd = ["find", *roots, "-iname", f"*{query}*", "-not", "-path", "*/.*"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        return []
    return [p for p in r.stdout.splitlines() if p.strip()][:limit]


@skill()
def find_files(
    query: Annotated[str, "Part of the file or folder name to look for, e.g. 'budget 2026'."],
    limit: Annotated[int, "Maximum number of results (1-20)."] = 8,
) -> str:
    """Find files or folders on this computer by name."""
    q = query.strip().strip("'\"")
    n = max(1, min(int(limit or 8), 20))
    if not q:
        return "Error: I need part of a file name to search for."
    try:
        paths = _find_with_spotlight(q, n) if platform.system() == "Darwin" else _find_with_find(q, n)
    except Exception as e:
        return f"Error: file search failed: {e}"
    if not paths:
        return f"I didn't find any files matching '{q}'."
    home = str(Path.home())
    shown = [p.replace(home, "~", 1) for p in paths]
    return f"Found {len(shown)} matching '{q}': " + "; ".join(shown)
