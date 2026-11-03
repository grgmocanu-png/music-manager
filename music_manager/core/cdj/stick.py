"""Stick CDJ: playlisturile scrise direct în formatul citit de playerele Pioneer, fără Rekordbox.

Pe stick:
  Contents/<Artist>/<fișier>                      piesele (numele fișierului rămâne cel original)
  PIONEER/rekordbox/export.pdb (+ exportExt.pdb)  baza de date (playlisturi, BPM, Key, taguri, coperte)
  PIONEER/USBANLZ/P.../..../ANLZ0000.DAT/EXT/2EX  beatgrid și waveform pentru fiecare piesă
  PIONEER/Artwork/00001/a<n>.jpg                  coperțile
  PIONEER/.manager-cdj.json                       ce a scris Manager (la sincronizare se șterge doar din astea)

Funcționează cu formatul „vechi” export.pdb, pe care îl citesc toate playerele (CDJ-350 … CDJ-3000, XDJ).
Planul (previzualizarea) se face fără să se scrie nimic; scrierea copiază doar ce e nou sau schimbat.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import audio as ca
from .pioneer import writer as anlz_writer
from .pioneer.layout import dedupe, sanitize_component
from .pioneer.model import BeatGrid, TrackAnalysis, TrackMeta
from .pioneer.pdb_builder import PdbBuilder

MANIFEST_REL = "PIONEER/.manager-cdj.json"
TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pioneer", "templates")
OLD_PLAYERS_NO_FLAC = "CDJ-350 / CDJ-850 / CDJ-2000 (primul model) nu citesc FLAC"


@dataclass
class StickTrack:
    src: str                 # fișierul de pe hard
    usb: str                 # calea pe stick, cu „/” la început (ex. /Contents/Artist/piesa.mp3)
    artist: str = ""
    title: str = ""
    album: str = ""
    genre: str = ""
    year: int = 0
    duration: float = 0.0
    bitrate: int = 0
    bpm: float = 0.0
    key: str = ""
    rating: int = 0
    grid_ms: float = -1.0    # din Traktor / Rekordbox, dacă e acolo
    grid_bpm: float = 0.0
    grid_beat: int = 1


@dataclass
class StickPlan:
    root: str
    tracks: list[StickTrack] = field(default_factory=list)
    playlists: list[tuple[str, list[str]]] = field(default_factory=list)   # (nume, [usb])
    copy: list[StickTrack] = field(default_factory=list)       # piese noi sau schimbate
    unchanged: int = 0
    delete: list[str] = field(default_factory=list)            # fișiere scrise de Manager care nu mai trebuie
    missing: list[str] = field(default_factory=list)           # piese care nu mai sunt pe hard
    flac: int = 0
    bytes_to_copy: int = 0
    free: int = 0

    @property
    def fits(self) -> bool:
        return self.free <= 0 or self.bytes_to_copy < self.free - 50_000_000


def _artist_dir(artist: str) -> str:
    return sanitize_component(artist or "Necunoscut", fallback="Necunoscut")


def load_manifest(root: str) -> dict:
    try:
        with open(os.path.join(root, MANIFEST_REL), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _same_file(src: str, dst: str) -> bool:
    try:
        a, b = os.stat(src), os.stat(dst)
    except OSError:
        return False
    return a.st_size == b.st_size and b.st_mtime >= a.st_mtime - 2      # FAT are precizie de 2 s


def plan(root: str, playlists: list[tuple[str, list[StickTrack]]], prune: bool = False) -> StickPlan:
    """Ce s-ar întâmpla la scriere — fără să atingă stickul. playlists: [(nume, [StickTrack fără usb])]."""
    p = StickPlan(root=root)
    old = load_manifest(root)
    by_src: dict[str, StickTrack] = {}
    taken: set[str] = set()
    # aceeași piesă de pe hard ajunge o singură dată pe stick, păstrând calea de data trecută (dacă există)
    prev = {os.path.normcase(v.get("src", "")): k for k, v in old.get("tracks", {}).items()}
    for name, items in playlists:
        usbs = []
        for t in items:
            key = os.path.normcase(os.path.abspath(t.src))
            if key in by_src:
                usbs.append(by_src[key].usb)
                continue
            if not os.path.isfile(t.src):
                p.missing.append(t.src)
                continue
            usb = prev.get(key) or f"/Contents/{_artist_dir(t.artist)}/{sanitize_component(os.path.basename(t.src))}"
            usb = dedupe(usb, taken)
            taken.add(usb.lower())
            t.usb = usb
            by_src[key] = t
            p.tracks.append(t)
            usbs.append(usb)
            if t.src.lower().endswith(".flac"):
                p.flac += 1
        p.playlists.append((name, usbs))
    for t in p.tracks:
        dst = os.path.join(root, t.usb.lstrip("/"))
        if _same_file(t.src, dst):
            p.unchanged += 1
        else:
            p.copy.append(t)
            try:
                p.bytes_to_copy += os.path.getsize(t.src)
            except OSError:
                pass
    if prune:
        keep = {t.usb.lower() for t in p.tracks}
        p.delete = sorted(u for u in old.get("tracks", {}) if u.lower() not in keep)
    try:
        p.free = shutil.disk_usage(root).free
    except OSError:
        p.free = 0
    return p


# --- analiza pentru stick (waveform + grid), cu cache ------------------------------------------
def _cache_path(cache_dir: str, src: str) -> str:
    st = os.stat(src)
    sig = f"{os.path.normcase(os.path.abspath(src))}|{st.st_size}|{int(st.st_mtime)}"
    return os.path.join(cache_dir, hashlib.sha1(sig.encode("utf-8", "replace")).hexdigest() + ".npz")


def _analysis(t: StickTrack, cache_dir: str, decode) -> tuple[dict, list[str]]:
    """{wave: WaveformData, first_ms, in_bar, bpm, duration_ms}; folosește cache-ul dacă fișierul nu s-a schimbat."""
    notes = []
    cp = _cache_path(cache_dir, t.src) if cache_dir else ""
    data = None
    if cp and os.path.exists(cp):
        try:
            z = np.load(cp)
            data = {k: z[k] for k in z.files}
        except (OSError, ValueError):
            data = None
    bpm = t.grid_bpm or t.bpm
    if data is None or (bpm and abs(float(data["bpm"]) - bpm) > 0.01 and t.grid_ms < 0):
        x = decode(t.src)
        w = ca.waveform(x)
        if not bpm:
            from ..analysis import estimate_bpm
            bpm, _c = estimate_bpm(x[::2], ca.SR // 2)
            notes.append("BPM calculat acum (nu era în Bibliotecă)")
        fb, inbar = ca.first_beat(x, bpm) if bpm else (0.0, 1)
        data = {"peak": np.array(w.peak, np.float32), "rms": np.array(w.rms, np.float32),
                "low": np.array(w.low, np.float32), "mid": np.array(w.mid, np.float32),
                "high": np.array(w.high, np.float32), "bpm": np.float64(bpm), "first_ms": np.float64(fb),
                "in_bar": np.int64(inbar), "duration_ms": np.float64(len(x) / ca.SR * 1000.0)}
        if cp:
            try:
                os.makedirs(cache_dir, exist_ok=True)
                np.savez(cp, **data)
            except OSError:
                pass
    from .pioneer.model import WaveformData
    wave = WaveformData(bins_per_second=ca.BINS_PER_SECOND, peak=tuple(data["peak"].tolist()),
                        rms=tuple(data["rms"].tolist()), low=tuple(data["low"].tolist()),
                        mid=tuple(data["mid"].tolist()), high=tuple(data["high"].tolist()))
    out = {"wave": wave, "bpm": float(data["bpm"]), "first_ms": float(data["first_ms"]),
           "in_bar": int(data["in_bar"]), "duration_ms": float(data["duration_ms"])}
    if t.grid_ms >= 0 and (t.grid_bpm or t.bpm):          # gridul din Traktor / Rekordbox are prioritate
        out["bpm"] = t.grid_bpm or t.bpm
        out["first_ms"], out["in_bar"] = ca.grid_from_anchor(t.grid_ms, out["bpm"], t.grid_beat)
    return out, notes


def _decode(src: str):
    from ..analysis import decode
    return decode(src, sr=ca.SR, timeout=180)


# --- coperți --------------------------------------------------------------------------------------
def _cover_bytes(src: str) -> bytes | None:
    try:
        from ..tags import read_tags
        td = read_tags(src, want_cover=True)
        return td.cover[0] if td.cover else None
    except Exception:  # noqa: BLE001
        return None


def _jpeg(data: bytes, side: int) -> bytes | None:
    """Pătrat (decupat pe centru), JPEG simplu, cu ffmpeg (fără biblioteci de imagini în plus)."""
    from ..convert import ffmpeg_path
    import tempfile
    ff = ffmpeg_path()
    if not ff:
        return None
    tmp = tempfile.mkdtemp(prefix="mm_cdj_")
    a, b = os.path.join(tmp, "in.img"), os.path.join(tmp, "out.jpg")
    try:
        with open(a, "wb") as f:
            f.write(data)
        kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        vf = f"crop='min(iw,ih)':'min(iw,ih)',scale={side}:{side},format=yuvj420p"
        subprocess.run([ff, "-v", "error", "-nostdin", "-y", "-i", a, "-vf", vf, "-frames:v", "1", "-q:v", "3", b],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                       timeout=30, **kw)
        with open(b, "rb") as f:
            out = f.read()
        return out if out[:3] == b"\xff\xd8\xff" else None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- scrierea -------------------------------------------------------------------------------------
def _key_name(key: str) -> str | None:
    from .pioneer.pdb_builder import key_name
    k = (key or "").strip()
    if not k:
        return None
    return key_name(k) if k[:-1].isdigit() and k[-1:].upper() in ("A", "B") else k


def _sample_rate(src: str) -> int:
    try:
        import mutagen
        f = mutagen.File(src)
        return int(getattr(f.info, "sample_rate", 0) or 44100)
    except Exception:  # noqa: BLE001
        return 44100


def write(p: StickPlan, cache_dir: str = "", progress=None, cancelled=None, decode=_decode) -> dict:
    """Scrie stickul după plan. Întoarce un raport: {copied, analyzed, deleted, errors, notes}."""
    progress = progress or (lambda *a: None)
    cancelled = cancelled or (lambda: False)
    root = p.root
    rep = {"copied": 0, "deleted": 0, "errors": [], "notes": []}
    old = load_manifest(root)
    manifest = {"app": "Music Manager", "tracks": {}, "files": {}}
    total = len(p.copy) + len(p.tracks) + 1
    step = 0

    # 1. piesele (doar cele noi sau schimbate)
    for t in p.copy:
        if cancelled():
            return rep
        dst = os.path.join(root, t.usb.lstrip("/"))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".partial"
            shutil.copy2(t.src, tmp)
            os.replace(tmp, dst)
            rep["copied"] += 1
        except OSError as e:
            rep["errors"].append(f"{os.path.basename(t.src)}: {e.strerror or e}")
        step += 1
        progress(step, total, f"Copiez {os.path.basename(t.src)}")

    # 2. analiza + ANLZ + rândurile din baza de date
    builder = PdbBuilder.from_template(os.path.join(TEMPLATES, "empty-export.pdb"))
    ids: dict[str, int] = {}
    art_ids: dict[str, int] = {}
    for t in p.tracks:
        if cancelled():
            return rep
        step += 1
        progress(step, total, f"Pregătesc {os.path.basename(t.src)}")
        dst = os.path.join(root, t.usb.lstrip("/"))
        if not os.path.isfile(dst):
            continue
        try:
            a, notes = _analysis(t, cache_dir, decode)
            rep["notes"] += [f"{os.path.basename(t.src)}: {n}" for n in notes]
        except Exception as e:  # noqa: BLE001
            rep["errors"].append(f"{os.path.basename(t.src)}: nu am putut citi sunetul ({e})")
            continue
        dur_ms = (t.duration * 1000.0) or a["duration_ms"]
        grid = BeatGrid.constant(bpm=a["bpm"], first_beat_ms=a["first_ms"], duration_ms=dur_ms,
                                 first_beat_in_bar=a["in_bar"], confidence=1.0) if a["bpm"] else None
        meta = TrackMeta(source_path=t.src, title=t.title or os.path.splitext(os.path.basename(t.src))[0],
                         artist=t.artist, album=t.album, genre=t.genre, year=t.year, duration_ms=dur_ms,
                         sample_rate=_sample_rate(t.src), bitrate=t.bitrate, file_size=os.path.getsize(dst))
        an = TrackAnalysis(meta=meta, grid=grid, waveform=a["wave"])
        try:
            written = anlz_writer.write_anlz(an, t.usb, Path(root))
        except OSError as e:
            rep["errors"].append(f"{os.path.basename(t.src)}: {e.strerror or e}")
            continue
        for path in written.values():
            rel = os.path.relpath(str(path), root).replace(os.sep, "/")
            manifest["files"][rel] = os.path.getsize(str(path))
        dat = "/" + os.path.relpath(str(written["ANLZ0000.DAT"]), root).replace(os.sep, "/")
        art = _artwork(t.src, root, art_ids, builder, manifest)
        ids[t.usb] = builder._editor.add_track(
            title=meta.title, file_path=t.usb, artist=t.artist or None, album=t.album or None,
            genre=t.genre or None, key=_key_name(t.key), analyze_path=dat,
            tempo=round(a["bpm"] * 100) if a["bpm"] else 0, duration=round(dur_ms / 1000), year=t.year,
            bitrate=t.bitrate, sample_rate=meta.sample_rate, file_size=meta.file_size,
            rating=max(0, min(5, t.rating)), artwork_id=art)
        manifest["tracks"][t.usb] = {"src": t.src, "size": meta.file_size}

    # 3. playlisturile
    for name, usbs in p.playlists:
        pid = builder.create_playlist(name or "Playlist")
        for u in usbs:
            if u in ids:
                builder.add_to_playlist(pid, ids[u])

    # 4. baza de date (scrisă la final, întâi ca fișier temporar)
    pdb_dir = os.path.join(root, "PIONEER", "rekordbox")
    os.makedirs(pdb_dir, exist_ok=True)
    tmp = os.path.join(pdb_dir, "export.pdb.partial")
    with open(tmp, "wb") as f:
        f.write(builder.to_bytes())
    os.replace(tmp, os.path.join(pdb_dir, "export.pdb"))
    shutil.copyfile(os.path.join(TEMPLATES, "empty-exportExt.pdb"), os.path.join(pdb_dir, "exportExt.pdb"))
    for n in ("export.pdb", "exportExt.pdb"):
        manifest["files"][f"PIONEER/rekordbox/{n}"] = os.path.getsize(os.path.join(pdb_dir, n))

    # 5. curățenie: doar fișiere scrise de Manager data trecută
    for u in p.delete:
        f = os.path.join(root, u.lstrip("/"))
        try:
            if os.path.isfile(f):
                os.remove(f)
                rep["deleted"] += 1
        except OSError as e:
            rep["errors"].append(f"{os.path.basename(f)}: {e.strerror or e}")
    keep = set(manifest["files"])
    for rel in old.get("files", {}):
        if rel not in keep and (rel.startswith("PIONEER/USBANLZ/") or rel.startswith("PIONEER/Artwork/")):
            try:
                os.remove(os.path.join(root, rel))
            except OSError:
                pass
    _remove_empty_dirs(os.path.join(root, "Contents"))
    _remove_empty_dirs(os.path.join(root, "PIONEER", "USBANLZ"))
    with open(os.path.join(root, MANIFEST_REL), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    rep["tracks"] = len(ids)
    step += 1
    progress(total, total, "Gata")
    return rep


def _artwork(src, root, art_ids, builder, manifest) -> int:
    raw = _cover_bytes(src)
    if not raw:
        return 0
    sha = hashlib.sha1(raw).hexdigest()
    if sha in art_ids:
        return art_ids[sha]
    small, medium = _jpeg(raw, 80), _jpeg(raw, 240)
    if not small or not medium:
        return 0
    n = len(art_ids) + 1
    rel = f"PIONEER/Artwork/00001/a{n}.jpg"
    for r, data in ((rel, small), (f"PIONEER/Artwork/00001/a{n}_m.jpg", medium)):
        dest = os.path.join(root, r)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        manifest["files"][r] = len(data)
    art_ids[sha] = builder.get_or_create_artwork("/" + rel)
    return art_ids[sha]


def _remove_empty_dirs(top: str) -> None:
    if not os.path.isdir(top):
        return
    for d, _subs, _files in sorted(os.walk(top), key=lambda w: -len(w[0])):
        if d != top:
            try:
                os.rmdir(d)          # doar dacă e gol
            except OSError:
                pass


def read_back(root: str) -> dict:
    """Citește înapoi baza de date de pe stick (cu cititorul de PDB, separat de partea care scrie):
    {tracks: [(titlu, cale, bpm, cheie)], playlists: {nume: [titluri]}}."""
    from .rekordbox_pdb import Database
    db = Database.from_file(os.path.join(root, "PIONEER", "rekordbox", "export.pdb"))
    keys = {k.id: k.name for k in db.keys}
    tracks = {t.id: t for t in db.tracks}
    out = {"tracks": [(t.title, t.file_path, t.tempo / 100, keys.get(t.key_id, "")) for t in db.tracks],
           "playlists": {}}
    names = {n.id: n.name for n in db.playlist_tree if not n.is_folder}
    for e in sorted(db.playlist_entries, key=lambda e: (e.playlist_id, e.entry_index)):
        if e.playlist_id in names and e.track_id in tracks:
            out["playlists"].setdefault(names[e.playlist_id], []).append(tracks[e.track_id].title)
    return out
