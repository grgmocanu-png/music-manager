"""Operații pe fișiere folosite din mai multe pagini (Bibliotecă, Explore)."""
from __future__ import annotations

import os
import shutil

from ..core import library as lib

TRASH_FALLBACK = "_De șters"


def delete_in_background(title: str, paths: list[str], on_done):
    """Trimite fișierele în Coș; pe discurile fără Coș le mută în „_De șters” lângă ele (nu le șterge definitiv).
    on_done(done, moved, errors) — rulează în firul interfeței."""
    from ..core.trash import split_by_bin, to_trash
    from .jobs import Job

    def work(progress, cancelled):
        ok, no = split_by_bin(paths)
        done, errors = to_trash(ok)
        moved = []
        for p in no:
            try:
                d = os.path.join(os.path.dirname(p), TRASH_FALLBACK)
                os.makedirs(d, exist_ok=True)
                t = lib.unique_path(os.path.join(d, os.path.basename(p)))
                shutil.move(p, t)
                moved.append((p, t))
            except OSError as e:
                errors.append(f"{os.path.basename(p)}: {e.strerror or e}")
        return done, moved, errors

    job = Job(title, work)
    job.succeeded.connect(lambda res: on_done(*res))
    job.start()
    return job


def delete_summary(done, moved) -> str:
    text = f"Am trimis {len(done)} fișiere în Coș."
    if moved:
        text += f" {len(moved)} (de pe discuri fără Coș) sunt în „{TRASH_FALLBACK}”."
    return text
