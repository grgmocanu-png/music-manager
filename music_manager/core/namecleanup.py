"""Curățarea numelor de fișiere (și, opțional, a tagurilor) de „gunoaie”.

Exemple:
  "01_artist_-_track_(www.mp3site.ru)_[320kbps].mp3"  -> "artist - track.mp3"
  "Track Name (Official Video) [FREE DOWNLOAD].mp3"   -> "Track Name.mp3"
"""
from __future__ import annotations

import os
import re

from .library import RenameItem, Track, safe_name

_PATTERNS = [
    r"https?://[^\s\)\]\}]+", r"\bwww\.[^\s\)\]\}]+", r"\b[\w-]+\.(com|net|org|ru|info|biz|me|cc|to|club|top|xyz)\b",
    r"[\(\[\{]\s*(official( music| lyric| hd)?( video| audio| visuali[sz]er)?|music video|lyrics?( video)?|"
    r"audio|video|hd|hq|4k|visuali[sz]er|free (download|dl)|free|out now|premiere|clip officiel|"
    r"\d{2,3} ?kbps|\d{2,3}k|mp3|flac|wav|320|explicit|clean|remastered|copy)\s*[\)\]\}]",
    r"\bfree download\b", r"\bfree dl\b", r"\b\d{3} ?kbps\b", r"\b(ytmp3|mp3juices|y2mate|savefrom|"
    r"mp3paw|zippyshare|mp3skull|tubidy|myfreemp3|soundcloud(down|mate)?)\b",
    r"\(\s*\d\s*\)$",            # „ (1)” de la copii duplicate
]
_JUNK = re.compile("|".join(_PATTERNS), re.I)


def clean_text(text: str, strip_track_numbers: bool = True) -> str:
    t = text or ""
    if "_" in t and " " not in t.strip():
        t = t.replace("_", " ")
    t = t.replace("_", " ")
    for _ in range(3):
        t = _JUNK.sub(" ", t)
    if strip_track_numbers:
        t = re.sub(r"^\s*(\d{1,3}|[A-D]\d{1,2})\s*[.\-_)\]]+\s*", "", t)      # „01 - ”, „A1. ”
        t = re.sub(r"^\s*0\d\s+(?=\S.*\s-\s)", "", t)                       # „01 Artist - Titlu”
    t = re.sub(r"\s+[-–—]+\s*|\s*[–—]+\s*|\s*-+\s+", " - ", t)               # separatori uniformi (nu „Jay-Z”)
    t = re.sub(r"(\s-\s)+", " - ", t)
    t = re.sub(r"[\(\[]\s*[\)\]]", "", t)                                    # paranteze goale
    t = re.sub(r"\s+", " ", t).strip(" -._")
    return t


def plan_name_cleanup(tracks: list[Track], strip_track_numbers: bool = True) -> list[RenameItem]:
    plan, taken = [], {}
    for t in tracks:
        stem, ext = os.path.splitext(os.path.basename(t.path))
        new = safe_name(clean_text(stem, strip_track_numbers)) or stem
        dest = os.path.join(os.path.dirname(t.path), new + ext.lower())
        if os.path.normcase(dest) == os.path.normcase(t.path) and dest == t.path:
            plan.append(RenameItem(t.path, dest, "same"))
            continue
        key = os.path.normcase(dest)
        other_exists = os.path.exists(dest) and os.path.normcase(dest) != os.path.normcase(t.path)
        if key in taken:
            plan.append(RenameItem(t.path, dest, "conflict", "același nume nou ca: " + taken[key]))
            continue
        if other_exists:
            plan.append(RenameItem(t.path, dest, "conflict", "există deja fișierul: " + dest))
            continue
        taken[key] = t.path
        plan.append(RenameItem(t.path, dest, "ok"))
    return plan


def clean_tag_fields(t: Track) -> dict:
    """Câmpurile artist/titlu/album curățate; întoarce doar ce s-a schimbat."""
    out = {}
    for f in ("artist", "title", "album"):
        v = getattr(t, f)
        if v:
            c = clean_text(v, strip_track_numbers=(f == "title"))
            if c and c != v:
                out[f] = c
    return out
