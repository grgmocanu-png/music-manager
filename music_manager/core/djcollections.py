"""Citirea colecțiilor din programele de DJ (doar citire — nu modificăm nimic).

- Traktor:   collection.nml (BPM, tonalitate, rating, culoare)
- Rekordbox: exportul XML (File → Export Collection in xml format)
- VirtualDJ: database.xml
- Serato și Mixed In Key își scriu BPM-ul/tonalitatea direct în fișiere,
  deci le vedem prin taguri.
"""
from __future__ import annotations

import glob
import os
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


TRAKTOR_KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
                "Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "A#m", "Bm"]

SOURCES = ("Traktor", "Rekordbox", "VirtualDJ")


def norm_path(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)) if p else ""


@dataclass
class DJInfo:
    bpm: float = 0.0
    key: str = ""
    rating: int = 0          # 0..5 stele
    color: str = ""
    genre: str = ""
    comment: str = ""


@dataclass
class Collection:
    source: str
    path: str
    tracks: dict = field(default_factory=dict)      # cale normalizată -> DJInfo
    playlists: dict = field(default_factory=dict)   # nume -> [căi normalizate]
    mtime: float = 0.0
    error: str = ""


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# --- Traktor -----------------------------------------------------------------
def find_traktor_collection(doc_dirs: list[str]) -> str:
    cands = []
    for d in doc_dirs:
        if d:
            cands += glob.glob(os.path.join(d, "Native Instruments", "Traktor*", "collection.nml"))
    cands = [c for c in cands if os.path.isfile(c)]
    return max(cands, key=os.path.getmtime) if cands else ""


def _traktor_location(loc: ET.Element) -> str:
    vol = loc.get("VOLUME", "")
    d = (loc.get("DIR", "") or "").replace("/:", "/")
    f = loc.get("FILE", "")
    if not f:
        return ""
    if len(vol) == 2 and vol[1] == ":":           # Windows: „C:”
        return vol + d + f
    p = d + f if d.startswith("/") else "/" + d + f       # macOS: VOLUME e numele discului
    if vol and not os.path.exists(p):
        ext = "/Volumes/" + vol + p                         # disc extern pe Mac
        if os.path.exists(ext):
            return ext
    return p


def parse_traktor(path: str) -> Collection:
    col = Collection("Traktor", path)
    try:
        col.mtime = os.path.getmtime(path)
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        col.error = str(e)
        return col
    by_key: dict[str, str] = {}
    for e in root.iter("ENTRY"):
        loc = e.find("LOCATION")
        if loc is None:
            continue
        p = _traktor_location(loc)
        if not p:
            continue
        info = DJInfo()
        tempo = e.find("TEMPO")
        if tempo is not None:
            info.bpm = round(_f(tempo.get("BPM")), 2)
        mk = e.find("MUSICAL_KEY")
        if mk is not None and str(mk.get("VALUE", "")).isdigit() and int(mk.get("VALUE")) < 24:
            info.key = TRAKTOR_KEYS[int(mk.get("VALUE"))]
        inf = e.find("INFO")
        if inf is not None:
            if not info.key and inf.get("KEY"):
                info.key = inf.get("KEY")
            r = _f(inf.get("RANKING"))
            info.rating = int(round(r / 51)) if r else 0
            info.color = inf.get("COLOR", "")
            info.genre = inf.get("GENRE", "")
            info.comment = inf.get("COMMENT", "")
        np_ = norm_path(p)
        col.tracks[np_] = info
        # cheia folosită în playlisturi: VOLUME + DIR + FILE
        by_key[(loc.get("VOLUME", "") + loc.get("DIR", "") + loc.get("FILE", ""))] = np_
    for node in root.iter("NODE"):
        if node.get("TYPE") != "PLAYLIST":
            continue
        name = node.get("NAME", "")
        paths = []
        for pk in node.iter("PRIMARYKEY"):
            k = pk.get("KEY", "")
            if k in by_key:
                paths.append(by_key[k])
        if name and paths:
            col.playlists[name] = paths
    return col


# --- Rekordbox ----------------------------------------------------------------
def _rb_location(loc: str) -> str:
    if not loc:
        return ""
    u = urllib.parse.urlparse(loc)
    p = urllib.parse.unquote(u.path)
    if len(p) > 3 and p[0] == "/" and p[2] == ":":       # /C:/...
        p = p[1:]
    return p


def parse_rekordbox_xml(path: str) -> Collection:
    col = Collection("Rekordbox", path)
    try:
        col.mtime = os.path.getmtime(path)
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        col.error = str(e)
        return col
    ids: dict[str, str] = {}
    coll = root.find("COLLECTION")
    for t in (coll.findall("TRACK") if coll is not None else []):
        p = _rb_location(t.get("Location", ""))
        if not p:
            continue
        r = _f(t.get("Rating"))
        info = DJInfo(bpm=round(_f(t.get("AverageBpm")), 2), key=t.get("Tonality", "") or "",
                      rating=int(round(r / 51)) if r else 0, color=t.get("Colour", "") or "",
                      genre=t.get("Genre", "") or "", comment=t.get("Comments", "") or "")
        np_ = norm_path(p)
        col.tracks[np_] = info
        if t.get("TrackID"):
            ids[t.get("TrackID")] = np_
    pls = root.find("PLAYLISTS")
    if pls is not None:
        for node in pls.iter("NODE"):
            if node.get("Type") == "1":
                paths = [ids[x.get("Key")] for x in node.findall("TRACK") if x.get("Key") in ids]
                if paths:
                    col.playlists[node.get("Name", "")] = paths
    return col


# --- VirtualDJ ------------------------------------------------------------------
def find_virtualdj_db(doc_dirs: list[str]) -> str:
    for d in doc_dirs:
        p = os.path.join(d, "VirtualDJ", "database.xml") if d else ""
        if p and os.path.isfile(p):
            return p
    return ""


def parse_virtualdj(path: str) -> Collection:
    col = Collection("VirtualDJ", path)
    try:
        col.mtime = os.path.getmtime(path)
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as e:
        col.error = str(e)
        return col
    for s in root.iter("Song"):
        p = s.get("FilePath", "")
        if not p:
            continue
        info = DJInfo()
        scan = s.find("Scan")
        if scan is not None:
            spb = _f(scan.get("Bpm"))          # VirtualDJ ține „secunde pe bătaie”
            if spb > 0:
                info.bpm = round(60.0 / spb, 2)
            info.key = scan.get("Key", "") or ""
        tags = s.find("Tags")
        if tags is not None:
            info.genre = tags.get("Genre", "") or ""
            st = _f(tags.get("Stars"))
            info.rating = int(st) if st else 0
        col.tracks[norm_path(p)] = info
    return col


# --- îmbinare --------------------------------------------------------------
def best_values(path: str, collections: list[Collection], priority: list[str]) -> tuple[DJInfo, dict]:
    """Valorile finale pentru o piesă, după ordinea de prioritate a surselor.

    Întoarce (DJInfo combinat, {câmp: sursă}).
    """
    np_ = norm_path(path)
    by_src = {c.source: c.tracks.get(np_) for c in collections if not c.error}
    out, src = DJInfo(), {}
    for name in priority:
        info = by_src.get(name)
        if not info:
            continue
        for f in ("bpm", "key", "rating", "color"):
            if not getattr(out, f) and getattr(info, f):
                setattr(out, f, getattr(info, f))
                src[f] = name
    return out, src
