"""Playlisturile Spotify salvate (butoanele din tabul Download) și starea lor."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class SavedPlaylist:
    id: str
    name: str
    url: str
    last_update: str = ""          # ISO
    last_total: int = 0
    last_missing: int = 0
    last_downloaded: int = 0
    ignored: list = field(default_factory=list)            # ID-uri Spotify ignorate definitiv
    custom_queries: dict = field(default_factory=dict)     # id -> [artist, titlu]
    not_found: dict = field(default_factory=dict)          # id -> {reason, code, date, track}
    seen: list = field(default_factory=list)               # piese văzute vreodată la tine (sau descărcate)
    baseline: str = ""     # piesele adăugate în Spotify înainte de data asta nu se mai bifează automat
    source: str = "spotify"                                # spotify | youtube
    first_seen: dict = field(default_factory=dict)         # id -> data primei verificări (pt. YouTube)

    def __post_init__(self):
        # compatibilitate cu versiunea în care not_found era id -> text
        for k, v in list(self.not_found.items()):
            if isinstance(v, str):
                self.not_found[k] = {"reason": v, "code": "", "date": "", "track": None}

    def stamp_dates(self, tracks) -> None:
        """YouTube nu spune când ai adăugat o piesă; folosim data la care am văzut-o prima oară."""
        now = datetime.now().isoformat(timespec="minutes")
        for t in tracks:
            if not t.added_at:
                t.added_at = self.first_seen.setdefault(t.id, now)

    @property
    def web_url(self) -> str:
        if self.source == "youtube":
            return self.url or f"https://www.youtube.com/playlist?list={self.id}"
        return self.url or f"https://open.spotify.com/playlist/{self.id}"

    def track_url(self, tid: str) -> str:
        return f"https://youtu.be/{tid}" if self.source == "youtube" else f"https://open.spotify.com/track/{tid}"

    def mark_seen(self, ids) -> None:
        s = set(self.seen)
        for i in ids:
            if i not in s:
                self.seen.append(i)
                s.add(i)

    @property
    def last_update_label(self) -> str:
        if not self.last_update:
            return "Niciodată actualizat"
        try:
            dt = datetime.fromisoformat(self.last_update)
        except ValueError:
            return self.last_update
        luni = ["ian", "feb", "mar", "apr", "mai", "iun", "iul", "aug", "sep", "oct", "nov", "dec"]
        return f"Ultimul update: {dt.day} {luni[dt.month - 1]} {dt.year}, {dt:%H:%M}"


def track_to_dict(t) -> dict:
    return {"id": t.id, "title": t.title, "artists": list(t.artists), "album": t.album,
            "duration": t.duration, "year": t.year, "isrc": t.isrc, "added_at": t.added_at,
            "cover_url": getattr(t, "cover_url", "")}


def track_from_dict(d: dict):
    from .spotify import SpotifyTrack
    return SpotifyTrack(id=d.get("id", ""), title=d.get("title", ""), artists=list(d.get("artists") or []),
                        album=d.get("album", ""), duration=float(d.get("duration") or 0),
                        year=d.get("year", ""), isrc=d.get("isrc", ""), added_at=d.get("added_at", ""),
                        cover_url=d.get("cover_url", ""))


# stări calculate la verificarea unui playlist
HAVE, IGNORED, NOTFOUND, DELETED, OLD, MISSING = "have", "ignored", "notfound", "deleted", "old", "missing"


def _parse_dt(text: str):
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)   # ora locală
    return dt


def classify(saved: SavedPlaylist, track, have_locally: bool) -> str:
    """Decide ce înseamnă o piesă din playlist pentru tine.

    - o ai               -> HAVE
    - ai ignorat-o       -> IGNORED
    - n-a fost găsită pe Soulseek data trecută -> NOTFOUND
    - a fost la tine, dar acum lipsește -> DELETED (ai șters-o tu, nu o mai descărcăm singuri)
    - adăugată în Spotify înainte de ultima descărcare -> OLD (nebifată automat)
    - altfel             -> MISSING (nouă, bifată automat)
    """
    if have_locally:
        return HAVE
    if track.id in saved.ignored:
        return IGNORED
    if track.id in saved.not_found:
        return NOTFOUND
    if track.id in saved.seen:
        return DELETED
    base = _parse_dt(saved.baseline)
    added = _parse_dt(track.added_at)
    if base and added and added < base:
        return OLD
    return MISSING


class PlaylistStore:
    def __init__(self, path: str):
        self.path = path
        self.items: list[SavedPlaylist] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for d in json.load(f).get("playlists", []):
                    try:
                        self.items.append(SavedPlaylist(**d))
                    except TypeError:
                        continue
        except (OSError, ValueError):
            pass

    def get(self, pid: str) -> SavedPlaylist | None:
        return next((p for p in self.items if p.id == pid), None)

    def add(self, p: SavedPlaylist) -> SavedPlaylist:
        existing = self.get(p.id)
        if existing:
            existing.name, existing.url = p.name or existing.name, p.url or existing.url
            self.save()
            return existing
        self.items.append(p)
        self.save()
        return p

    def remove(self, pid: str) -> None:
        self.items = [p for p in self.items if p.id != pid]
        self.save()

    def move(self, pid: str, delta: int) -> None:
        i = next((k for k, p in enumerate(self.items) if p.id == pid), None)
        if i is None:
            return
        j = max(0, min(len(self.items) - 1, i + delta))
        self.items.insert(j, self.items.pop(i))
        self.save()

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"playlists": [asdict(p) for p in self.items]}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
