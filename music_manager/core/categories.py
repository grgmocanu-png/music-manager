"""„Curăță categorii”: operații pe valorile unui câmp (gen, comentariu, artist, label, an...).

Trei operații, pentru orice câmp:
  - șterge un text (ex. „Danceability” din Comments), cu tot cu cifrele lipite de el;
  - înlocuiește un text cu altul (ex. „Deep house” → „Deep House”);
  - unește mai multe valori într-una singură.
"""
from __future__ import annotations

import re

from .library import Track

FIELDS = {
    "genre": "Genre",
    "comment": "Comments",
    "artist": "Artist",
    "label": "Label",
    "year": "Year",
    "album": "Album",
}

_SEPS = "|,;/"


def tidy(value: str) -> str:
    """Curăță ce rămâne după o ștergere: spații duble, separatori goi la capete sau dublați."""
    v = " ".join((value or "").split())
    # separatori consecutivi („a |  | b” → „a | b”)
    v = re.sub(r"\s*([|;/,])\s*(?:[|;/,]\s*)+", r" \1 ", v)
    v = re.sub(r"\s+([,;])", r"\1", v)
    v = re.sub(r"\s*\|\s*", " | ", v)
    v = v.strip(" " + _SEPS + "-–")
    v = re.sub(r"\(\s*\)|\[\s*\]", "", v)
    return " ".join(v.split()).strip(" " + _SEPS + "-–")


def remove_text(value: str, text: str, with_numbers: bool = True) -> str:
    if not text.strip() or not value:
        return value
    pat = re.escape(text.strip())
    if with_numbers:
        pat = r"(?:\d+(?:[.,]\d+)?\s*[:=-]?\s*)?" + pat + r"(?:\s*[:=-]?\s*\d+(?:[.,]\d+)?%?)?"
    new = re.sub(pat, " ", value, flags=re.I)
    return tidy(new) if new != value else value


def replace_text(value: str, old: str, new: str) -> str:
    if not old or not value:
        return value
    out = re.sub(re.escape(old), new.replace("\\", "\\\\"), value, flags=re.I)
    return " ".join(out.split()) if out != value else value


def value_counts(values) -> list[tuple[str, int]]:
    c: dict[str, int] = {}
    for v in values:
        c[v or ""] = c.get(v or "", 0) + 1
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0].lower()))


def plan(items, field: str, fn) -> list[tuple[str, str, str]]:
    """[(cale, valoare veche, valoare nouă)] doar pentru piesele care se schimbă."""
    out = []
    for it in items:
        if getattr(it, "missing", False) or getattr(it, "offline", False):
            continue
        t = it if isinstance(it, Track) else it.track
        old = getattr(t, field, "") or ""
        new = fn(old)
        if new != old:
            out.append((t.path, old, new))
    return out
