"""Trimite fișiere în Coșul de gunoi (Windows: Recycle Bin, Mac: Trash) — se pot recupera."""
from __future__ import annotations

import os
import shutil
import sys


def to_trash(paths: list[str]) -> tuple[list[str], list[str]]:
    """Întoarce (trimise, erori)."""
    done, errors = [], []
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
        for p in paths:
            op = SHFILEOPSTRUCTW()
            op.wFunc = FO_DELETE
            op.pFrom = os.path.abspath(p) + "\0"
            op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
            r = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
            if r == 0 and not os.path.exists(p):
                done.append(p)
            else:
                errors.append(f"{os.path.basename(p)}: nu a putut fi trimis în Coș (cod {r})")
        return done, errors
    trash = os.path.expanduser("~/.Trash" if sys.platform == "darwin" else "~/.local/share/Trash/files")
    os.makedirs(trash, exist_ok=True)
    for p in paths:
        try:
            dest = os.path.join(trash, os.path.basename(p))
            n = 2
            base, ext = os.path.splitext(dest)
            while os.path.exists(dest):
                dest = f"{base} {n}{ext}"
                n += 1
            shutil.move(p, dest)
            done.append(p)
        except OSError as e:
            errors.append(f"{os.path.basename(p)}: {e.strerror or e}")
    return done, errors
