"""Trimite fișiere în Coșul de gunoi (Windows: Recycle Bin, Mac: Trash) — se pot recupera."""
from __future__ import annotations

import os
import shutil
import sys


DRIVE_FIXED = 3


def has_recycle_bin(path: str) -> bool:
    """Windows are Coș doar pe discurile „fixe” (inclusiv hardurile externe pe USB).
    Pe stick-uri, carduri și foldere din rețea fișierele ar fi șterse definitiv."""
    if sys.platform != "win32":
        return True                     # Mac / Linux: mutăm noi în Trash (se copiază dacă e alt disc)
    try:
        import ctypes
        root = os.path.splitdrive(os.path.abspath(path))[0]
        if not root or root.startswith("\\\\"):
            return False                # cale de rețea
        return ctypes.windll.kernel32.GetDriveTypeW(root + "\\") == DRIVE_FIXED
    except Exception:  # noqa: BLE001
        return False


def split_by_bin(paths: list[str]) -> tuple[list[str], list[str]]:
    """(se pot trimite în Coș, n-au Coș pe discul lor)."""
    ok, no = [], []
    for p in paths:
        (ok if has_recycle_bin(p) else no).append(p)
    return ok, no


def to_trash(paths: list[str]) -> tuple[list[str], list[str]]:
    """Întoarce (trimise, erori). Nu șterge niciodată definitiv: dacă un fișier nu poate
    ajunge în Coș (disc fără Coș, Coș plin), nu e atins și apare la erori."""
    done, errors = [], []
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
        FOF_WANTNUKEWARNING = 0x4000   # dacă ar fi ștearsă definitiv, Windows întreabă înainte
        for p in paths:
            if not has_recycle_bin(p):
                errors.append(f"{os.path.basename(p)}: discul nu are Coș de gunoi — fișierul n-a fost atins")
                continue
            op = SHFILEOPSTRUCTW()
            op.wFunc = FO_DELETE
            op.pFrom = os.path.abspath(p) + "\0"
            op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_WANTNUKEWARNING | FOF_SILENT | FOF_NOERRORUI
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
