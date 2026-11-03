"""Path planning for the USB image: FAT-safe Contents/<Artist>/<Album>/ tree."""

from __future__ import annotations

import re
from pathlib import Path

from .model import TrackMeta

_FAT_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
_MAX_COMPONENT = 120  # headroom under the 255-UTF-16 LFN limit


def sanitize_component(name: str, fallback: str = "Unknown") -> str:
    clean = _FAT_FORBIDDEN.sub("_", name).strip().rstrip(". ")
    if not clean or clean.upper() in _RESERVED:
        clean = fallback
    return clean[:_MAX_COMPONENT].rstrip(". ")


def usb_audio_path(meta: TrackMeta) -> str:
    """USB-relative audio path with leading slash (pdb/ANLZ convention)."""
    artist = sanitize_component(meta.artist or "Unknown Artist")
    album = sanitize_component(meta.album or "Unknown Album")
    ext = Path(meta.source_path).suffix.lower() or ".mp3"
    stem = sanitize_component(meta.title, fallback=Path(meta.source_path).stem)
    return f"/Contents/{artist}/{album}/{stem}{ext}"


def dedupe(path: str, taken: set[str]) -> str:
    """Deterministic ' (2)', ' (3)'… suffixing on case-insensitive collision."""
    if path.lower() not in taken:
        return path
    stem, dot, ext = path.rpartition(".")
    n = 2
    while f"{stem} ({n}).{ext}".lower() in taken:
        n += 1
    return f"{stem} ({n}).{ext}"
