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
    not_found: dict = field(default_factory=dict)          # id -> motiv (de la ultima încercare)

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
