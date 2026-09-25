"""Simple persistent notes ("remember that ...")."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated

from .context import SkillContext
from .registry import skill


def _notes_path(ctx: SkillContext) -> Path:
    return ctx.settings.data_dir / "notes.json"


def _load(ctx: SkillContext) -> list[dict]:
    p = _notes_path(ctx)
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return []


def _save(ctx: SkillContext, notes: list[dict]) -> None:
    p = _notes_path(ctx)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(notes, indent=2))


@skill()
def remember(note: Annotated[str, "The thing to remember, in the user's words."], ctx: SkillContext = None) -> str:
    """Save a note for later ("remember that the gate code is 4412")."""
    text = note.strip().rstrip(".")
    if not text:
        return "Error: nothing to remember."
    notes = _load(ctx)
    notes.append({"text": text, "when": datetime.now().isoformat(timespec="minutes")})
    _save(ctx, notes)
    return f"Noted: {text}."


@skill()
def recall_notes(query: Annotated[str, "Optional keyword to filter notes; empty for all."] = "", ctx: SkillContext = None) -> str:
    """Read back saved notes, optionally filtered by a keyword."""
    notes = _load(ctx)
    q = (query or "").strip().lower()
    if q:
        notes = [n for n in notes if q in n["text"].lower()]
    if not notes:
        return "You have no saved notes." if not q else f"No notes mention {query}."
    recent = notes[-10:]
    return f"You have {len(notes)} note{'s' if len(notes) != 1 else ''}. " + " ".join(
        f"{i}. {n['text']}." for i, n in enumerate(recent, 1)
    )


@skill()
def forget_notes(query: Annotated[str, "Keyword of the note(s) to delete, or 'all'."], ctx: SkillContext = None) -> str:
    """Delete saved notes matching a keyword (or all of them)."""
    notes = _load(ctx)
    q = query.strip().lower()
    if q == "all":
        _save(ctx, [])
        return f"Deleted all {len(notes)} notes."
    keep = [n for n in notes if q not in n["text"].lower()]
    removed = len(notes) - len(keep)
    _save(ctx, keep)
    return f"Deleted {removed} note{'s' if removed != 1 else ''}." if removed else f"No notes mention {query}."
