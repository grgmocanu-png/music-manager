"""Conversie audio cu ffmpeg (inclus în aplicație), păstrând tagurile și coperta."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from . import tags as tagmod
from .library import unique_path


@dataclass(frozen=True)
class Preset:
    label: str
    ext: str
    args: tuple
    lossless: bool
    bits16: bool = False


PRESETS = {
    "mp3_320": Preset("MP3 320 kbps (cea mai bună calitate MP3, ideal DJ)", ".mp3",
                      ("-c:a", "libmp3lame", "-b:a", "320k"), False),
    "mp3_v0": Preset("MP3 V0 (~245 kbps, fișiere mai mici)", ".mp3",
                     ("-c:a", "libmp3lame", "-q:a", "0"), False),
    "wav16": Preset("WAV 16-bit (calitate CD, merge peste tot)", ".wav",
                    ("-c:a", "pcm_s16le"), True, bits16=True),
    "wav24": Preset("WAV 24-bit (păstrează rezoluția înaltă)", ".wav",
                    ("-c:a", "pcm_s24le"), True),
    "aiff16": Preset("AIFF 16-bit (ca WAV, dar cu taguri mai sigure în Rekordbox)", ".aiff",
                     ("-c:a", "pcm_s16be"), True, bits16=True),
    "aiff24": Preset("AIFF 24-bit", ".aiff", ("-c:a", "pcm_s24be"), True),
    "flac": Preset("FLAC (fără pierderi, comprimat)", ".flac",
                   ("-c:a", "flac", "-compression_level", "8"), True),
}

SAMPLE_RATES = {"Păstrează originalul": 0, "44.1 kHz (CD)": 44100, "48 kHz": 48000}

IF_EXISTS = ("skip", "rename", "overwrite")


def ffmpeg_path() -> str | None:
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    return shutil.which("ffmpeg")


@dataclass
class ConvertJob:
    src: str
    dest: str


@dataclass
class ConvertResult:
    src: str
    dest: str
    ok: bool
    message: str = ""
    skipped: bool = False


def plan_jobs(files: list[str], preset_key: str, out_dir: str | None,
              base_dir: str | None = None, name_template: str | None = None,
              clean_names: bool = False) -> list[ConvertJob]:
    """Calculează unde va ajunge fiecare fișier convertit.

    out_dir None  -> lângă fișierul original
    out_dir dat   -> în out_dir, păstrând structura de foldere față de base_dir
    """
    preset = PRESETS[preset_key]
    jobs = []
    for src in files:
        stem = os.path.splitext(os.path.basename(src))[0]
        if name_template or clean_names:
            from .library import render_template, safe_name, track_from_file
            from .namecleanup import clean_text
            new = None
            if name_template:
                try:
                    new = render_template(name_template, track_from_file(src))
                except Exception:
                    new = None
            if not new:
                new = safe_name(clean_text(stem)) if clean_names else stem
            stem = (new or stem).replace("/", " - ")
        if out_dir:
            sub = ""
            if base_dir:
                try:
                    rel = os.path.relpath(os.path.dirname(src), base_dir)
                    if not rel.startswith(".."):
                        sub = "" if rel == "." else rel
                except ValueError:  # alt disc pe Windows
                    sub = ""
            dest = os.path.join(out_dir, sub, stem + preset.ext)
        else:
            dest = os.path.join(os.path.dirname(src), stem + preset.ext)
        jobs.append(ConvertJob(src, os.path.normpath(dest)))
    return jobs


def _run_ffmpeg(cmd: list[str]) -> tuple[int, str]:
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       stdin=subprocess.DEVNULL, **kwargs)
    return p.returncode, p.stderr.decode("utf-8", "ignore")


def convert_one(job: ConvertJob, preset_key: str, sample_rate: int = 0,
                if_exists: str = "skip", ffmpeg: str | None = None) -> ConvertResult:
    preset = PRESETS[preset_key]
    ffmpeg = ffmpeg or ffmpeg_path()
    if not ffmpeg:
        return ConvertResult(job.src, job.dest, False, "ffmpeg nu a fost găsit")

    src, dest = job.src, job.dest
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dest)):
        return ConvertResult(src, dest, False, "Fișierul e deja în acest format", skipped=True)
    if os.path.exists(dest):
        if if_exists == "skip":
            return ConvertResult(src, dest, True, "Există deja — sărit", skipped=True)
        if if_exists == "rename":
            dest = unique_path(dest)

    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    stem, ext = os.path.splitext(dest)
    tmp = stem + ".mmpart" + ext

    fmt = {".mp3": "mp3", ".wav": "wav", ".aiff": "aiff", ".flac": "flac"}[preset.ext]
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", src,
           "-map", "0:a:0", "-map_metadata", "-1", "-vn"]
    cmd += list(preset.args)
    filters = []
    if preset.bits16:
        filters.append("aresample=dither_method=triangular")
    if filters:
        cmd += ["-af", ",".join(filters)]
    if sample_rate:
        cmd += ["-ar", str(sample_rate)]
    cmd += ["-f", fmt, tmp]

    code, err = _run_ffmpeg(cmd)
    if code != 0 or not os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
        last = [l for l in err.strip().splitlines() if l.strip()][-1:] or ["eroare necunoscută"]
        return ConvertResult(src, dest, False, "ffmpeg: " + last[0])

    tag_msg = ""
    try:
        data = tagmod.read_tags(src, want_cover=True)
        tagmod.write_tags(tmp, data.fields, data.cover)
    except Exception as e:
        tag_msg = f" (tagurile nu au putut fi copiate: {e})"

    os.replace(tmp, dest)
    return ConvertResult(src, dest, True, "Gata" + tag_msg)


def convert_many(jobs: list[ConvertJob], preset_key: str, sample_rate: int = 0,
                 if_exists: str = "skip", workers: int | None = None,
                 progress: Callable[[ConvertResult, int, int], None] | None = None,
                 cancelled: Callable[[], bool] | None = None) -> list[ConvertResult]:
    ff = ffmpeg_path()
    workers = workers or max(1, min(4, (os.cpu_count() or 2) // 2))
    results: list[ConvertResult] = []
    total = len(jobs)
    # destinații duplicate în același lot (ex. song.flac și song.wav în același folder)
    seen: set[str] = set()
    fixed = []
    for j in jobs:
        key = os.path.normcase(j.dest)
        if key in seen:
            stem, ext = os.path.splitext(j.dest)
            n = 2
            while os.path.normcase(f"{stem} ({n}){ext}") in seen:
                n += 1
            j = ConvertJob(j.src, f"{stem} ({n}){ext}")
            key = os.path.normcase(j.dest)
        seen.add(key)
        fixed.append(j)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(convert_one, j, preset_key, sample_rate, if_exists, ff): j for j in fixed}
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            if progress:
                progress(r, len(results), total)
            if cancelled and cancelled():
                for f in futures:
                    f.cancel()
                break
    return results
