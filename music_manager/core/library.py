"""Biblioteca: scanarea folderelor, cache, dubluri și redenumire."""
from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Callable, Iterable

from . import tags as tagmod

CACHE_VERSION = 3


@dataclass
class Track:
    path: str
    artist: str = ""
    title: str = ""
    album: str = ""
    albumartist: str = ""
    genre: str = ""
    year: str = ""
    track: str = ""
    bpm: str = ""
    key: str = ""
    comment: str = ""
    label: str = ""
    has_cover: bool = False
    duration: float = 0.0
    bitrate: int = 0
    sample_rate: int = 0
    bits_per_sample: int = 0
    lossless: bool = False
    size: int = 0
    mtime: float = 0.0
    error: str = ""

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].lower().lstrip(".")

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def display_title(self) -> str:
        return self.title or os.path.splitext(self.filename)[0]

    @property
    def quality_label(self) -> str:
        if self.lossless:
            bits = f"{self.bits_per_sample}-bit " if self.bits_per_sample else ""
            sr = f"{self.sample_rate / 1000:g} kHz" if self.sample_rate else ""
            return f"{self.ext.upper()} {bits}{sr}".strip()
        return f"{self.ext.upper()} {self.bitrate} kbps" if self.bitrate else self.ext.upper()

    def quality_score(self) -> float:
        """Cu cât mai mare, cu atât mai bună calitatea (pentru dubluri)."""
        score = float(self.bitrate)
        if self.lossless:
            score += 10_000 + self.bits_per_sample * 10 + self.sample_rate / 1000
        return score


def track_from_file(path: str) -> Track:
    st = os.stat(path)
    t = Track(path=path, size=st.st_size, mtime=st.st_mtime)
    try:
        data = tagmod.read_tags(path)
        for k, v in data.fields.items():
            setattr(t, k, v)
        t.has_cover = data.has_cover
        i = data.info
        t.duration, t.bitrate = i.duration, i.bitrate
        t.sample_rate, t.bits_per_sample, t.lossless = i.sample_rate, i.bits_per_sample, i.lossless
    except Exception as e:  # fișier stricat sau format ciudat
        t.error = str(e) or e.__class__.__name__
        t.lossless = ("." + t.ext) in tagmod.LOSSLESS_EXTENSIONS
    return t


def find_audio_files(folders: Iterable[str]) -> list[str]:
    out = []
    for folder in folders:
        for root, dirs, files in os.walk(folder):
            # ignorăm folderele ascunse și pe cel de dubluri mutate
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != DUPLICATES_FOLDER]
            for f in files:
                if not f.startswith("._") and tagmod.is_audio_file(f) and not is_temp_file(f):
                    out.append(os.path.join(root, f))
    out.sort(key=lambda p: p.lower())
    return out


class LibraryCache:
    """Ține minte tagurile citite, ca a doua scanare să fie aproape instantă."""

    def __init__(self, path: str):
        self.path = path
        self.items: dict[str, dict] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") == CACHE_VERSION:
                self.items = data.get("tracks", {})
        except Exception:
            self.items = {}

    def get(self, path: str) -> Track | None:
        d = self.items.get(path)
        if not d:
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        if d.get("size") != st.st_size or abs(d.get("mtime", 0) - st.st_mtime) > 1:
            return None
        try:
            return Track(**d)
        except TypeError:
            return None

    def put(self, track: Track) -> None:
        self.items[track.path] = asdict(track)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": CACHE_VERSION, "tracks": self.items}, f)
        os.replace(tmp, self.path)


def scan(folders: Iterable[str], cache: LibraryCache | None = None,
         progress: Callable[[int, int], None] | None = None,
         cancelled: Callable[[], bool] | None = None,
         workers: int = 8) -> list[Track]:
    files = find_audio_files(folders)
    total = len(files)
    results: list[Track | None] = [None] * total
    todo = []
    for i, p in enumerate(files):
        t = cache.get(p) if cache else None
        if t is not None:
            results[i] = t
        else:
            todo.append(i)
    done = total - len(todo)
    if progress:
        progress(done, total)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        it = ex.map(lambda i: (i, track_from_file(files[i])), todo)
        for i, t in it:
            results[i] = t
            if cache:
                cache.put(t)
            done += 1
            if progress and (done % 25 == 0 or done == total):
                progress(done, total)
            if cancelled and cancelled():
                ex.shutdown(wait=False, cancel_futures=True)
                break
    if cache:
        cache.save()
    return [t for t in results if t is not None]


# --- dubluri -------------------------------------------------------------
DUPLICATES_FOLDER = "_Dubluri"

_FEAT_RE = re.compile(r"\s*[\(\[]?\s*(feat\.?|ft\.?|featuring)\s+[^\)\]]*[\)\]]?", re.I)
_BRACKET_NOISE = re.compile(
    r"[\(\[]\s*(original mix|remaster(ed)?( \d{4})?|\d{4} remaster(ed)?|radio edit|"
    r"explicit|clean|album version|single version|mono|stereo)\s*[\)\]]", re.I)


def normalize(text: str) -> str:
    """Text simplificat pentru comparații: fără diacritice, majuscule, „feat.” etc."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = _BRACKET_NOISE.sub(" ", text)
    text = _FEAT_RE.sub(" ", text)
    text = text.replace("&", " and ")
    text = re.sub(r"^the\s+", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def artist_title_from_filename(t: Track) -> tuple[str, str]:
    """Artistul și titlul din numele fișierului („04. Artist - Titlu (Remix).wav”), cu literele originale."""
    stem = os.path.splitext(t.filename)[0].replace("_", " ")
    stem = re.sub(r"^\s*\d{1,3}(?:\s*[.)\-–]+\s*|\s+)", "", stem)
    if " - " not in stem:
        return "", ""
    a, b = stem.split(" - ", 1)
    return " ".join(a.split()), " ".join(b.split())


def _identity(t: Track) -> tuple[str, str] | None:
    artist, title = t.artist, t.title
    if not (artist and title):
        # încercăm din numele fișierului: "Artist - Titlu"
        stem = os.path.splitext(t.filename)[0]
        stem = re.sub(r"^\d+[\s.\-_]+", "", stem)  # număr de piesă la început
        if " - " in stem:
            a, b = stem.split(" - ", 1)
            artist, title = artist or a, title or b
        else:
            return None
    a, ti = normalize(artist), normalize(title)
    if not a or not ti:
        return None
    return a, ti


def find_duplicates(tracks: list[Track], check_duration: bool = True) -> list[list[Track]]:
    """Grupuri de piese care par aceeași înregistrare (artist + titlu).

    Dacă durata diferă cu mai mult de 4 secunde, nu le considerăm dubluri
    (poate fi un remix, o versiune extinsă sau live).
    Fiecare grup e sortat cu cea mai bună calitate prima.
    """
    by_id: dict[tuple, list[Track]] = {}
    for t in tracks:
        ident = _identity(t)
        if ident:
            by_id.setdefault(ident, []).append(t)

    groups = []
    for items in by_id.values():
        if len(items) < 2:
            continue
        if not check_duration:
            clusters = [items]
        else:
            clusters: list[list[Track]] = []
            for t in sorted(items, key=lambda x: x.duration):
                for c in clusters:
                    if not t.duration or not c[0].duration or abs(t.duration - c[0].duration) <= 4:
                        c.append(t)
                        break
                else:
                    clusters.append([t])
        for c in clusters:
            if len(c) > 1:
                c.sort(key=lambda x: x.quality_score(), reverse=True)
                groups.append(c)
    groups.sort(key=lambda g: (normalize(g[0].artist), normalize(g[0].title)))
    return groups


def move_to_duplicates(paths: list[str], log_dir: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Mută fișierele într-un folder „_Dubluri” lângă ele (nu șterge nimic).

    Întoarce (mutări reușite, erori).
    """
    moves, errors = [], []
    for p in paths:
        try:
            dest_dir = os.path.join(os.path.dirname(p), DUPLICATES_FOLDER)
            os.makedirs(dest_dir, exist_ok=True)
            dest = unique_path(os.path.join(dest_dir, os.path.basename(p)))
            shutil.move(p, dest)
            moves.append((p, dest))
        except OSError as e:
            errors.append(f"{os.path.basename(p)}: {e.strerror or e}")
    if moves:
        _write_undo_log(log_dir, "dubluri", moves)
    return moves, errors


# --- redenumire ----------------------------------------------------------
RENAME_PRESETS = {
    "Artist - Titlu": "{artist} - {title}",
    "Nr. Artist - Titlu": "{track:02} {artist} - {title}",
    "Artist/Album/Nr. Titlu (foldere)": "{albumartist}/{album}/{track:02} {title}",
    "Gen/Artist - Titlu (foldere)": "{genre}/{artist} - {title}",
    "Artist - Titlu [BPM Cheie] (DJ)": "{artist} - {title} [{bpm} {key}]",
}

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PLACEHOLDER = re.compile(r"\{(\w+)(?::(\d+))?\}")


def safe_name(text: str) -> str:
    text = _ILLEGAL.sub("", text.replace("/", "-").replace("\\", "-"))
    text = " ".join(text.split()).strip(" .")
    return text[:150]


def render_template(template: str, t: Track) -> str | None:
    """Construiește noua cale relativă (fără extensie). None dacă lipsesc taguri esențiale."""
    values = {
        "artist": t.artist, "title": t.title, "album": t.album,
        "albumartist": t.albumartist or t.artist, "genre": t.genre,
        "year": t.year, "track": t.track, "bpm": t.bpm, "key": t.key,
    }
    missing = False

    def repl(m: re.Match) -> str:
        nonlocal missing
        name, width = m.group(1), m.group(2)
        v = values.get(name, "")
        if not v:
            if name in ("artist", "title", "albumartist"):
                missing = True
            return ""
        if width and v.isdigit():
            v = v.zfill(int(width))
        return safe_name(v)

    parts = []
    for part in template.split("/"):
        s = _PLACEHOLDER.sub(repl, part)
        s = re.sub(r"([\[\(])\s+", r"\1", s)
        s = re.sub(r"\s+([\]\)])", r"\1", s)
        s = re.sub(r"\[\]|\(\)", "", s)                # paranteze rămase goale
        s = re.sub(r"\s+-\s*$|^\s*-\s+", "", s)        # liniuțe orfane
        s = safe_name(s)
        if not s:
            s = "Necunoscut"
        parts.append(s)
    if missing:
        return None
    return "/".join(parts)


@dataclass
class RenameItem:
    src: str
    dest: str
    status: str = "ok"   # ok | same | missing | conflict
    note: str = ""       # de ce (ce tag lipsește / cu ce fișier s-ar ciocni)


def plan_renames(tracks: list[Track], template: str, base_dir: str | None = None) -> list[RenameItem]:
    """Calculează redenumirile fără să atingă nimic pe disc.

    Dacă șablonul conține „/”, fișierele sunt organizate în foldere sub
    base_dir (implicit: folderul în care se află fiecare fișier).
    """
    plan: list[RenameItem] = []
    taken: dict[str, str] = {}
    for t in tracks:
        rel = render_template(template, t)
        if rel is None:
            need = [n for n, v in (("artist", t.artist), ("titlu", t.title)) if not v]
            plan.append(RenameItem(t.path, t.path, "missing", "lipsește " + " și ".join(need or ["artistul"])))
            continue
        root = base_dir if (base_dir and "/" in template) else os.path.dirname(t.path)
        dest = os.path.normpath(os.path.join(root, *rel.split("/"))) + os.path.splitext(t.path)[1].lower()
        if os.path.normcase(dest) == os.path.normcase(t.path):
            plan.append(RenameItem(t.path, dest, "same"))
            continue
        key = os.path.normcase(dest)
        exists_other = os.path.exists(dest) and not same_file(dest, t.path)
        if key in taken:
            plan.append(RenameItem(t.path, dest, "conflict", "același nume nou ca: " + taken[key]))
            continue
        if exists_other:
            plan.append(RenameItem(t.path, dest, "conflict", "există deja fișierul: " + dest))
            continue
        taken[key] = t.path
        plan.append(RenameItem(t.path, dest, "ok"))
    return plan


def apply_renames(plan: list[RenameItem], log_dir: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Aplică planul. Întoarce (mutări reușite, erori). Nu suprascrie niciodată."""
    moves, errors = [], []
    for item in plan:
        if item.status != "ok":
            continue
        try:
            same = os.path.normcase(item.dest) == os.path.normcase(item.src)
            if os.path.exists(item.dest) and not same:
                errors.append(f"{os.path.basename(item.dest)}: există deja")
                continue
            os.makedirs(os.path.dirname(item.dest), exist_ok=True)
            if same:
                # doar schimbare de majuscule (Windows / macOS nu fac diferența)
                tmp = item.src + ".mmtmp"
                os.rename(item.src, tmp)
                os.rename(tmp, item.dest)
            else:
                shutil.move(item.src, item.dest)
            moves.append((item.src, item.dest))
            _remove_empty_dirs(os.path.dirname(item.src))
        except OSError as e:
            errors.append(f"{os.path.basename(item.src)}: {e.strerror or e}")
    if moves:
        _write_undo_log(log_dir, "redenumire", moves)
    return moves, errors


def _remove_empty_dirs(d: str) -> None:
    try:
        while d and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
    except OSError:
        pass


def same_file(a: str, b: str) -> bool:
    """True dacă a și b sunt același fișier (ex. diferă doar majusculele pe Windows / Mac)."""
    try:
        return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except OSError:
        return False


def move_or_recase(src: str, dest: str) -> str:
    """Mută fișierul; dacă destinația e același fișier cu alte majuscule, doar schimbă majusculele."""
    if os.path.abspath(src) == os.path.abspath(dest):
        return dest
    if same_file(src, dest):
        tmp = src + ".mmtmp"
        os.rename(src, tmp)
        os.rename(tmp, dest)
        return dest
    dest = unique_path(dest)
    shutil.move(src, dest)
    return dest


TEMP_SUFFIXES = (".incomplete", ".mmtmp", ".tmp", ".part")


def is_temp_file(name: str) -> bool:
    n = name.lower()
    return ".mmpart." in n or n.endswith(TEMP_SUFFIXES)


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


# --- anulare -------------------------------------------------------------
def _write_undo_log(log_dir: str, kind: str, moves: list[tuple[str, str]]) -> None:
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    with open(os.path.join(log_dir, f"{stamp}_{kind}.json"), "w", encoding="utf-8") as f:
        json.dump({"kind": kind, "time": stamp, "moves": moves}, f, ensure_ascii=False, indent=1)


def last_undo(log_dir: str) -> tuple[str, dict] | None:
    try:
        logs = sorted(f for f in os.listdir(log_dir) if f.endswith(".json"))
    except OSError:
        return None
    if not logs:
        return None
    path = os.path.join(log_dir, logs[-1])
    with open(path, "r", encoding="utf-8") as f:
        return path, json.load(f)


def undo_last(log_dir: str) -> tuple[int, int, str]:
    """Anulează ultima operație. Întoarce (reușite, eșuate, tipul operației)."""
    found = last_undo(log_dir)
    if not found:
        return 0, 0, ""
    path, data = found
    ok = fail = 0
    for src, dest in reversed(data["moves"]):
        try:
            if os.path.exists(dest) and not os.path.exists(src):
                os.makedirs(os.path.dirname(src), exist_ok=True)
                shutil.move(dest, src)
                _remove_empty_dirs(os.path.dirname(dest))
                ok += 1
            else:
                fail += 1
        except OSError:
            fail += 1
    os.replace(path, path + ".anulat")
    return ok, fail, data.get("kind", "")
