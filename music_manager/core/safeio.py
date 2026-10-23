"""Scriere sigură a fișierelor JSON ale aplicației.

Pe Windows, antivirusul, indexarea sau altă salvare pot ține fișierul ocupat
o clipă; `os.replace` dă atunci PermissionError (WinError 32/5). Aici:
- o singură salvare pe fișier odată (lacăt per cale);
- fișier temporar unic, ca două salvări să nu se calce;
- reîncercări timp de câteva secunde; la final, scriere directă.
"""
from __future__ import annotations

import json
import os
import threading
import time

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    k = os.path.normcase(os.path.abspath(path))
    with _guard:
        lk = _locks.get(k)
        if lk is None:
            lk = _locks[k] = threading.Lock()
        return lk


def replace_retry(src: str, dst: str, tries: int = 30, delay: float = 0.1) -> None:
    """os.replace cu reîncercări când fișierul e ocupat."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(delay * (1 + i // 5))


def write_text(path: str, text: str) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with _lock_for(path):
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            replace_retry(tmp, path)
        except PermissionError:
            # ultima variantă: scriem direct peste (fișierul rămâne valid
            # dacă nimeni nu îl ține blocat la scriere)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def write_json(path: str, obj, **kw) -> None:
    write_text(path, json.dumps(obj, **kw))
