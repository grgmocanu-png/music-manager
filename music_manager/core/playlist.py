"""Filtrarea pieselor și salvarea/încărcarea playlisturilor M3U."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from .library import Track, normalize

# Roata Camelot <-> notație muzicală, pentru mixaj armonic
CAMELOT = {
    "1A": "G#m", "1B": "B", "2A": "D#m", "2B": "F#", "3A": "A#m", "3B": "C#",
    "4A": "Fm", "4B": "G#", "5A": "Cm", "5B": "D#", "6A": "Gm", "6B": "A#",
    "7A": "Dm", "7B": "F", "8A": "Am", "8B": "C", "9A": "Em", "9B": "G",
    "10A": "Bm", "10B": "D", "11A": "F#m", "11B": "A", "12A": "C#m", "12B": "E",
}
_ENHARMONIC = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}
_NOTE_TO_CAMELOT = {v.upper(): k for k, v in CAMELOT.items()}


def to_camelot(key: str) -> str:
    """Transformă orice notație de tonalitate în Camelot ("Am" -> "8A")."""
    k = (key or "").strip().upper().replace(" ", "")
    if not k:
        return ""
    if re.fullmatch(r"(1[0-2]|[1-9])[AB]", k):
        return k
    m = re.fullmatch(r"([A-G])([#B♯♭]?)(MIN(OR)?|MAJ(OR)?|M)?", k.replace("♯", "#").replace("♭", "B"))
    if not m:
        # format Open Key ("8m", "1d")
        m2 = re.fullmatch(r"(1[0-2]|[1-9])([MD])", k)
        if m2:
            n = (int(m2.group(1)) + 6) % 12 + 1
            return f"{n}{'A' if m2.group(2) == 'M' else 'B'}"
        return ""
    note = m.group(1) + (m.group(2) or "")
    note = _ENHARMONIC.get(note, note)
    minor = (m.group(3) or "").startswith("M") and not (m.group(3) or "").startswith("MAJ")
    return _NOTE_TO_CAMELOT.get(note + ("M" if minor else ""), "")


def compatible_keys(camelot: str) -> set[str]:
    """Tonalitățile care se mixează bine cu cea dată (aceeași, ±1, relativa)."""
    m = re.fullmatch(r"(1[0-2]|[1-9])([AB])", camelot or "")
    if not m:
        return set()
    n, letter = int(m.group(1)), m.group(2)
    up, down = n % 12 + 1, (n - 2) % 12 + 1
    other = "B" if letter == "A" else "A"
    return {f"{n}{letter}", f"{up}{letter}", f"{down}{letter}", f"{n}{other}"}


@dataclass
class Filter:
    text: str = ""
    genre: str = ""
    year_min: int = 0
    year_max: int = 0
    bpm_min: int = 0
    bpm_max: int = 0
    key: str = ""           # Camelot; se acceptă și tonalitățile compatibile
    harmonic: bool = False  # dacă True, include tonalitățile compatibile
    format: str = ""        # "mp3", "flac", "lossless", ...

    def matches(self, t: Track) -> bool:
        if self.text:
            hay = normalize(" ".join([t.artist, t.title, t.album, t.filename, t.comment]))
            if not all(w in hay for w in normalize(self.text).split()):
                return False
        if self.genre and normalize(self.genre) not in normalize(t.genre):
            return False
        if self.year_min or self.year_max:
            if not t.year.isdigit():
                return False
            y = int(t.year)
            if self.year_min and y < self.year_min:
                return False
            if self.year_max and y > self.year_max:
                return False
        if self.bpm_min or self.bpm_max:
            if not t.bpm.isdigit():
                return False
            b = int(t.bpm)
            ok = (not self.bpm_min or b >= self.bpm_min) and (not self.bpm_max or b <= self.bpm_max)
            # acceptăm și jumătate/dublu de tempo (ex. 70 BPM pentru un filtru 140)
            if not ok and self.bpm_min and self.bpm_max:
                ok = any(self.bpm_min <= x <= self.bpm_max for x in (b * 2, b / 2))
            if not ok:
                return False
        if self.key:
            tk = to_camelot(t.key)
            if not tk:
                return False
            allowed = compatible_keys(self.key) if self.harmonic else {self.key}
            if tk not in allowed:
                return False
        if self.format:
            if self.format == "lossless":
                if not t.lossless:
                    return False
            elif self.format == "lossy":
                if t.lossless:
                    return False
            elif t.ext != self.format:
                return False
        return True


def apply_filter(tracks: list[Track], flt: Filter) -> list[Track]:
    return [t for t in tracks if flt.matches(t)]


def genres_of(tracks: list[Track]) -> list[str]:
    seen: dict[str, str] = {}
    for t in tracks:
        for g in re.split(r"[;/,]", t.genre):
            g = g.strip()
            if g and normalize(g) not in seen:
                seen[normalize(g)] = g
    return sorted(seen.values(), key=str.lower)


# --- M3U ------------------------------------------------------------------
def write_m3u(path: str, tracks: list[Track], relative: bool = True) -> None:
    """Salvează un playlist M3U8 (UTF-8), citit de Rekordbox, Serato, VLC, foobar etc."""
    base = os.path.dirname(os.path.abspath(path))
    lines = ["#EXTM3U"]
    for t in tracks:
        secs = int(round(t.duration)) if t.duration else -1
        name = f"{t.artist} - {t.display_title}" if t.artist else t.display_title
        lines.append(f"#EXTINF:{secs},{name}")
        p = os.path.abspath(t.path)
        if relative:
            try:
                rel = os.path.relpath(p, base)
                p = rel
            except ValueError:   # alt disc pe Windows
                pass
        lines.append(p)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def read_m3u(path: str) -> list[str]:
    """Întoarce căile absolute din playlist (cele care nu există sunt incluse și ele)."""
    base = os.path.dirname(os.path.abspath(path))
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                content = f.read()
            break
        except UnicodeDecodeError:
            continue
    out = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("file://"):
            line = unquote(urlparse(line).path)
            if re.match(r"^/[A-Za-z]:/", line):   # file:///C:/...
                line = line[1:]
        p = line.replace("\\", os.sep) if os.sep == "/" else line.replace("/", os.sep)
        if not os.path.isabs(p) and not re.match(r"^[A-Za-z]:", p):
            p = os.path.normpath(os.path.join(base, p))
        out.append(p)
    return out
