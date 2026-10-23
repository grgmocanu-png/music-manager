"""Coperte compatibile cu Rekordbox / CDJ / Traktor: JPEG simplu (baseline), max. 600×600."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

MAX_SIDE = 600


def normalize_cover(data: bytes) -> tuple[bytes, str] | None:
    """Orice imagine (JPEG progresiv, PNG, WebP…) → JPEG baseline ≤600 px. None dacă nu e o imagine."""
    if not data:
        return None
    from .convert import ffmpeg_path
    ff = ffmpeg_path()
    if not ff:
        return (data, "image/jpeg") if data[:3] == b"\xff\xd8\xff" else None
    tmp = tempfile.mkdtemp(prefix="mm_cover_")
    src, dst = os.path.join(tmp, "in.img"), os.path.join(tmp, "out.jpg")
    try:
        with open(src, "wb") as f:
            f.write(data)
        kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        vf = (f"scale='min({MAX_SIDE},iw)':'min({MAX_SIDE},ih)':force_original_aspect_ratio=decrease,"
              "format=yuvj420p")
        subprocess.run([ff, "-v", "error", "-nostdin", "-y", "-i", src, "-vf", vf, "-frames:v", "1",
                        "-c:v", "mjpeg", "-q:v", "3", dst],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                       timeout=30, **kwargs)
        with open(dst, "rb") as f:
            out = f.read()
        return (out, "image/jpeg") if out[:3] == b"\xff\xd8\xff" else None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        for p in (src, dst):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass
