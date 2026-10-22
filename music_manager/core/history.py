"""Istoricul de curățenie: prin ce pași a trecut deja fiecare piesă.

Ca să nu repeți la nesfârșit aceleași operații pe aceleași fișiere. Istoricul
urmează fișierul când e redenumit sau mutat de aplicație.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

STEPS = {
    "dubluri": "dubluri verificate",
    "nume": "nume curățat",
    "taguri": "taguri căutate online",
    "redenumire": "redenumită / organizată",
}


def _k(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


class CleanupHistory:
    def __init__(self, path: str):
        self.path = path
        self.data: dict[str, dict[str, str]] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.data = json.load(f).get("files", {})
        except (OSError, ValueError):
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"files": self.data}, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def done(self, path: str, step: str) -> str:
        """Data la care s-a făcut pasul (sau "" dacă nu s-a făcut)."""
        return (self.data.get(_k(path)) or {}).get(step, "")

    def steps(self, path: str) -> dict:
        return dict(self.data.get(_k(path)) or {})

    def mark(self, paths, step: str, save: bool = True):
        now = datetime.now().isoformat(timespec="minutes")
        for p in paths:
            if p:
                self.data.setdefault(_k(p), {})[step] = now
        if save:
            self.save()

    def unmark(self, paths, step: str):
        for p in paths:
            d = self.data.get(_k(p))
            if d:
                d.pop(step, None)
        self.save()

    def moved(self, moves: list[tuple[str, str]], save: bool = True):
        for old, new in moves:
            d = self.data.pop(_k(old), None)
            if d:
                self.data.setdefault(_k(new), {}).update(d)
        if save:
            self.save()

    def label(self, path: str) -> str:
        st = self.steps(path)
        short = {"dubluri": "dubluri", "nume": "nume", "taguri": "taguri", "redenumire": "redenumită"}
        parts = [f"{short[s]} ✓" for s in STEPS if s in st]
        return " · ".join(parts)

    def complete(self, path: str) -> bool:
        st = self.steps(path)
        return all(s in st for s in STEPS)
