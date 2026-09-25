"""Spotting the assistant's name in a transcript, tolerant of how speech-to-text spells it.

"Daxton" comes back from Whisper as "Daxton", "Dexton", "Daxon" or "Daxten" depending on the
speaker and the room, so the match is fuzzy (difflib ratio) and accepts configured aliases.
"""

from __future__ import annotations

import difflib
import re

_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def name_variants(name: str, aliases: list[str] | tuple[str, ...] = ()) -> list[str]:
    out = []
    for n in [name, *aliases]:
        n = (n or "").strip().lower()
        if n and n not in out:
            out.append(n)
    return out


def _matches(token: str, variants: list[str], fuzzy: float) -> bool:
    t = token.lower().strip("'-")
    if not t:
        return False
    for v in variants:
        if t == v:
            return True
        if len(t) >= 4 and len(v) >= 4 and abs(len(t) - len(v)) <= 2 \
                and difflib.SequenceMatcher(None, t, v).ratio() >= fuzzy:
            return True
    return False


def find_name(text: str, name: str, aliases=(), fuzzy: float = 0.8) -> re.Match | None:
    """The first word (as a regex match over `text`) that is the name, an alias, or close enough."""
    variants = name_variants(name, aliases)
    for m in _WORD.finditer(text or ""):
        if _matches(m.group(0), variants, fuzzy):
            return m
    return None


def contains_name(text: str, name: str, aliases=(), fuzzy: float = 0.8) -> bool:
    return find_name(text, name, aliases, fuzzy) is not None


def strip_name(text: str, name: str, aliases=(), fuzzy: float = 0.8) -> str:
    """Remove every mention of the name plus the 'hey' / 'ok' that tends to precede it, tidy punctuation."""
    variants = name_variants(name, aliases)
    words = []
    for token in re.split(r"(\s+)", text or ""):
        if token.strip() and _matches(token.strip(".,!?;:"), variants, fuzzy):
            continue
        words.append(token)
    out = "".join(words)
    out = re.sub(r"^\W+|\W+$", "", out.strip())
    # "hey Daxton, open Safari" -> "open Safari"; but a bare "hello Daxton" keeps its greeting
    rest = re.sub(r"^(?:hey|ok|okay|hi|hello|yo)\b[\s,]*", "", out, flags=re.I)
    if rest.strip():
        out = rest
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip(" ,")
