"""Baza de date a Bibliotecii: date în plus față de taguri, playlisturi, reguli, export.

Ce ține minte (fișier JSON în folderul aplicației):
- pentru fiecare piesă: rating, culoare, data adăugării, rezultatul analizei
- playlisturile manuale și cele inteligente (cu reguli)
Tagurile (artist, titlu, gen…) rămân în fișiere — sursa principală.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from .djcollections import Collection, best_values, norm_path
from .library import Track, normalize
from .playlist import compatible_keys, to_camelot

DEFAULT_PRIORITY = ["Traktor", "Rekordbox", "VirtualDJ", "Fișier", "Analiză"]


@dataclass
class Extra:
    rating: int = 0
    color: str = ""
    date_added: str = ""
    an_bpm: float = 0.0           # din analiza noastră
    an_key: str = ""
    an_drops: list = field(default_factory=list)
    user_bpm: float = 0.0         # setat manual (are prioritate maximă)
    user_key: str = ""


@dataclass
class Playlist:
    id: str
    name: str
    kind: str = "manual"          # manual | smart
    tracks: list = field(default_factory=list)    # căi (manual)
    rules: dict = field(default_factory=lambda: {"match": "all", "rules": []})


@dataclass
class LibItem:
    """O piesă așa cum o vede Biblioteca: tagurile + valorile alese din toate sursele."""
    track: Track
    bpm: float = 0.0
    key: str = ""
    rating: int = 0
    color: str = ""
    bpm_src: str = ""
    key_src: str = ""
    date_added: str = ""
    in_traktor: bool = False
    drops: list = field(default_factory=list)
    missing: bool = False
    offline: bool = False

    @property
    def path(self) -> str:
        return self.track.path

    @property
    def camelot(self) -> str:
        return to_camelot(self.key)

    @property
    def style(self) -> str:
        return self.track.comment


class LibraryDB:
    def __init__(self, path: str):
        self.path = path
        self.extras: dict[str, Extra] = {}
        self.playlists: list[Playlist] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in (data.get("extras") or {}).items():
                try:
                    self.extras[k] = Extra(**v)
                except TypeError:
                    pass
            for p in data.get("playlists") or []:
                try:
                    self.playlists.append(Playlist(**p))
                except TypeError:
                    pass
        except (OSError, ValueError):
            pass

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"extras": {k: asdict(v) for k, v in self.extras.items()},
                       "playlists": [asdict(p) for p in self.playlists]}, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def extra(self, path: str) -> Extra:
        k = norm_path(path)
        e = self.extras.get(k)
        if e is None:
            e = self.extras[k] = Extra(date_added=datetime.now().isoformat(timespec="minutes"))
        return e

    # --- playlisturi ---
    def new_playlist(self, name: str, kind: str = "manual") -> Playlist:
        p = Playlist(id=uuid.uuid4().hex[:12], name=name, kind=kind)
        self.playlists.append(p)
        return p

    def get_playlist(self, pid: str) -> Playlist | None:
        return next((p for p in self.playlists if p.id == pid), None)

    def delete_playlist(self, pid: str):
        self.playlists = [p for p in self.playlists if p.id != pid]

    def relink(self, old: str, new: str) -> int:
        """O piesă s-a mutat: actualizăm playlisturile și datele în plus."""
        n = 0
        ok, nk = norm_path(old), norm_path(new)
        for p in self.playlists:
            for i, t in enumerate(p.tracks):
                if norm_path(t) == ok:
                    p.tracks[i] = new
                    n += 1
        if ok in self.extras and nk not in self.extras:
            self.extras[nk] = self.extras.pop(ok)
        return n


# --- combinarea surselor ------------------------------------------------------
def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def build_items(tracks: list[Track], db: LibraryDB, collections: list[Collection],
                priority: list[str] | None = None) -> list[LibItem]:
    priority = priority or DEFAULT_PRIORITY
    dj_priority = [p for p in priority if p not in ("Fișier", "Analiză")]
    traktor = next((c for c in collections if c.source == "Traktor" and not c.error), None)
    items = []
    for t in tracks:
        e = db.extra(t.path)
        dj, dj_src = best_values(t.path, collections, dj_priority)
        cand = {
            "Fișier": (_f(t.bpm), t.key),
            "Analiză": (e.an_bpm, e.an_key),
        }
        bpm, key, bsrc, ksrc = 0.0, "", "", ""
        if e.user_bpm:
            bpm, bsrc = e.user_bpm, "Manual"
        if e.user_key:
            key, ksrc = e.user_key, "Manual"
        for src in priority:
            if src in cand:
                b, k = cand[src]
            elif dj_src.get("bpm") == src or dj_src.get("key") == src:
                b = dj.bpm if dj_src.get("bpm") == src else 0
                k = dj.key if dj_src.get("key") == src else ""
            else:
                continue
            if not bpm and b:
                bpm, bsrc = float(b), src
            if not key and k:
                key, ksrc = k, src
        it = LibItem(track=t, bpm=round(bpm, 2), key=key, bpm_src=bsrc, key_src=ksrc,
                     rating=e.rating or dj.rating, color=e.color or dj.color, date_added=e.date_added,
                     in_traktor=bool(traktor and norm_path(t.path) in traktor.tracks), drops=e.an_drops)
        items.append(it)
    return items


# --- reguli pentru playlisturi inteligente -----------------------------------
RULE_FIELDS = {
    "genre": "Gen", "style": "Stil / comentariu", "bpm": "BPM", "key": "Tonalitate", "year": "An",
    "rating": "Rating (stele)", "added": "Adăugată", "format": "Format", "artist": "Artist",
    "title": "Titlu", "label": "Label", "folder": "Folder", "in_traktor": "E în Traktor",
    "has_cover": "Are copertă",
}
RULE_OPS = {
    "genre": ["este oricare din", "nu este"], "style": ["conține", "nu conține"],
    "bpm": ["între"], "key": ["este", "compatibilă cu"], "year": ["între"], "rating": ["cel puțin"],
    "added": ["în ultimele N zile"], "format": ["este oricare din"], "artist": ["conține", "nu conține"],
    "title": ["conține", "nu conține"], "label": ["conține"], "folder": ["conține"],
    "in_traktor": ["da", "nu"], "has_cover": ["da", "nu"],
}


def _bpm_in(b: float, lo: float, hi: float) -> bool:
    if not b:
        return False
    if lo and hi:
        return any(lo <= x <= hi for x in (b, b * 2, b / 2))
    return (not lo or b >= lo) and (not hi or b <= hi)


def _list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def rule_matches(it: LibItem, rule: dict) -> bool:
    f, op, v = rule.get("field"), rule.get("op", ""), rule.get("value", "")
    t = it.track
    if f == "genre":
        genres = {normalize(g) for g in re.split(r"[;/,]", t.genre) if g.strip()}
        wanted = {normalize(g) for g in _list(v)}
        hit = bool(genres & wanted)
        return hit if op != "nu este" else not hit
    if f in ("style", "artist", "title", "label", "folder"):
        hay = {"style": t.comment, "artist": t.artist, "title": t.display_title, "label": t.label,
               "folder": os.path.dirname(t.path)}[f]
        hit = normalize(str(v)) in normalize(hay)
        return hit if op != "nu conține" else not hit
    if f == "bpm":
        lo, hi = (_f(x) for x in (list(v) + [0, 0])[:2]) if isinstance(v, list) else (_f(v), 0)
        return _bpm_in(it.bpm, lo, hi)
    if f == "year":
        lo, hi = (int(_f(x)) for x in (list(v) + [0, 0])[:2]) if isinstance(v, list) else (int(_f(v)), 0)
        if not t.year.isdigit():
            return False
        y = int(t.year)
        return (not lo or y >= lo) and (not hi or y <= hi)
    if f == "key":
        cam = it.camelot
        target = to_camelot(str(v))
        if not cam or not target:
            return False
        return cam in compatible_keys(target) if op == "compatibilă cu" else cam == target
    if f == "rating":
        return it.rating >= int(_f(v))
    if f == "added":
        try:
            d = datetime.fromisoformat(it.date_added)
        except (TypeError, ValueError):
            return False
        return d >= datetime.now() - timedelta(days=_f(v) or 30)
    if f == "format":
        fm = {x.lower().lstrip(".") for x in _list(v)}
        return t.ext in fm or ("lossless" in fm and t.lossless)
    if f == "in_traktor":
        return it.in_traktor == (op == "da")
    if f == "has_cover":
        return t.has_cover == (op == "da")
    return True


def smart_filter(items: list[LibItem], rules: dict) -> list[LibItem]:
    rs = rules.get("rules") or []
    if not rs:
        return list(items)
    if rules.get("match", "all") == "any":
        return [it for it in items if any(rule_matches(it, r) for r in rs)]
    return [it for it in items if all(rule_matches(it, r) for r in rs)]


def describe_rule(rule: dict) -> str:
    v = rule.get("value")
    if isinstance(v, list):
        v = " – ".join(str(x) for x in v if str(x))
    return f"{RULE_FIELDS.get(rule.get('field'), rule.get('field'))} {rule.get('op', '')} {v}".strip()


# --- piese care se mixează ---------------------------------------------------
def mixable(base: LibItem, items: list[LibItem], bpm_tol_pct: float = 6.0) -> list[LibItem]:
    cam = base.camelot
    keys = compatible_keys(cam) if cam else set()
    out = []
    for it in items:
        if it is base or it.path == base.path:
            continue
        if keys and it.camelot not in keys:
            continue
        if base.bpm and it.bpm:
            ratio = min(abs(it.bpm * m - base.bpm) / base.bpm for m in (1, 2, 0.5)) * 100
            if ratio > bpm_tol_pct:
                continue
        elif base.bpm:
            continue
        out.append(it)

    def dist(it):
        kd = 0 if it.camelot == cam else 1
        bd = min(abs(it.bpm * m - base.bpm) for m in (1, 2, 0.5)) if base.bpm and it.bpm else 99
        return (kd, bd)
    return sorted(out, key=dist)


# --- export ---------------------------------------------------------------
def _file_url(path: str) -> str:
    p = os.path.abspath(path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    return "file://localhost" + urllib.parse.quote(p, safe="/:")


def export_rekordbox_xml(path: str, playlists: list[tuple[str, list[LibItem]]]) -> None:
    """Un fișier XML pe care Rekordbox îl citește (Preferences → Advanced → rekordbox xml)."""
    root = ET.Element("DJ_PLAYLISTS", Version="1.0.0")
    ET.SubElement(root, "PRODUCT", Name="Music Manager", Version="1", Company="")
    ids: dict[str, int] = {}
    tracks: list[LibItem] = []
    for _, items in playlists:
        for it in items:
            k = norm_path(it.path)
            if k not in ids:
                ids[k] = len(ids) + 1
                tracks.append(it)
    coll = ET.SubElement(root, "COLLECTION", Entries=str(len(tracks)))
    for it in tracks:
        t = it.track
        attrs = {
            "TrackID": str(ids[norm_path(it.path)]), "Name": t.display_title, "Artist": t.artist,
            "Album": t.album, "Genre": t.genre, "Kind": t.ext.upper() + " File",
            "Size": str(t.size), "TotalTime": str(int(t.duration)), "Year": t.year,
            "AverageBpm": f"{it.bpm:.2f}" if it.bpm else "0.00", "BitRate": str(t.bitrate),
            "SampleRate": str(t.sample_rate), "Comments": t.comment, "Rating": str(min(5, it.rating) * 51),
            "Location": _file_url(it.path), "Tonality": it.key, "Label": t.label,
        }
        ET.SubElement(coll, "TRACK", {k: v for k, v in attrs.items() if v is not None})
    pls = ET.SubElement(root, "PLAYLISTS")
    rootnode = ET.SubElement(pls, "NODE", Type="0", Name="ROOT", Count=str(len(playlists)))
    for name, items in playlists:
        node = ET.SubElement(rootnode, "NODE", Name=name, Type="1", KeyType="0", Entries=str(len(items)))
        for it in items:
            ET.SubElement(node, "TRACK", Key=str(ids[norm_path(it.path)]))
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


TRAKTOR_KEY_INDEX = {k: i for i, k in enumerate(
    ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
     "Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "A#m", "Bm"])}


def _traktor_loc(path: str) -> tuple[str, str, str]:
    p = os.path.abspath(path).replace("\\", "/")
    vol = ""
    if len(p) > 1 and p[1] == ":":
        vol, p = p[:2], p[2:]
    d, f = p.rsplit("/", 1)
    tdir = "/:".join(d.split("/")) + "/:"
    if not tdir.startswith("/:"):
        tdir = "/:" + tdir
    tdir = tdir.replace("/:/:", "/:")
    return vol, tdir, f


def export_traktor_nml(path: str, name: str, items: list[LibItem]) -> None:
    """Un playlist în format Traktor (.nml), de importat din Traktor (Import Playlist).

    Piesele care există deja în colecția Traktor își păstrează cue-urile și grid-ul.
    """
    from .playlist import CAMELOT  # noqa: F401
    root = ET.Element("NML", VERSION="19")
    ET.SubElement(root, "HEAD", COMPANY="www.native-instruments.com", PROGRAM="Traktor")
    ET.SubElement(root, "MUSICFOLDERS")
    coll = ET.SubElement(root, "COLLECTION", ENTRIES=str(len(items)))
    keys = []
    for it in items:
        t = it.track
        vol, d, f = _traktor_loc(it.path)
        e = ET.SubElement(coll, "ENTRY", TITLE=t.display_title, ARTIST=t.artist)
        ET.SubElement(e, "LOCATION", DIR=d, FILE=f, VOLUME=vol, VOLUMEID=vol)
        if t.album:
            ET.SubElement(e, "ALBUM", TITLE=t.album)
        info = {"GENRE": t.genre, "COMMENT": t.comment, "LABEL": t.label, "RANKING": str(min(5, it.rating) * 51)}
        ET.SubElement(e, "INFO", {k: v for k, v in info.items() if v})
        if it.bpm:
            ET.SubElement(e, "TEMPO", BPM=f"{it.bpm:.6f}", BPM_QUALITY="100.000000")
        k = it.key
        norm = to_camelot(k)
        if norm:
            from .playlist import CAMELOT as CAM
            note = CAM[norm]
            if note in TRAKTOR_KEY_INDEX:
                ET.SubElement(e, "MUSICAL_KEY", VALUE=str(TRAKTOR_KEY_INDEX[note]))
        keys.append(vol + d + f)
    ET.SubElement(root, "SETS", ENTRIES="0")
    pls = ET.SubElement(root, "PLAYLISTS")
    folder = ET.SubElement(pls, "NODE", TYPE="FOLDER", NAME="$ROOT")
    sub = ET.SubElement(folder, "SUBNODES", COUNT="1")
    node = ET.SubElement(sub, "NODE", TYPE="PLAYLIST", NAME=name)
    pl = ET.SubElement(node, "PLAYLIST", ENTRIES=str(len(items)), TYPE="LIST", UUID=uuid.uuid4().hex)
    for k in keys:
        ent = ET.SubElement(pl, "ENTRY")
        ET.SubElement(ent, "PRIMARYKEY", TYPE="TRACK", KEY=k)
    ET.ElementTree(root).write(path, encoding="UTF-8", xml_declaration=True)


def export_csv(path: str, items: list[LibItem]) -> None:
    import csv
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Artist", "Titlu", "Gen", "Stil", "BPM", "Tonalitate", "Camelot", "An", "Label", "Rating", "Cale"])
        for it in items:
            t = it.track
            w.writerow([t.artist, t.display_title, t.genre, t.comment, it.bpm or "", it.key, it.camelot,
                        t.year, t.label, it.rating or "", it.path])


# --- curățare genuri / artiști -----------------------------------------------
def genre_counts(items: list[LibItem]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for it in items:
        g = it.track.genre.strip()
        counts[g] = counts.get(g, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


def artist_groups(items: list[LibItem]) -> list[list[tuple[str, int]]]:
    """Grupuri de nume de artist care par același artist scris diferit."""
    by_norm: dict[str, dict[str, int]] = {}
    for it in items:
        a = it.track.artist.strip()
        if not a:
            continue
        key = re.sub(r"^the ", "", normalize(a)).replace(" and ", " ")
        key = key.replace(" ", "")
        by_norm.setdefault(key, {})
        by_norm[key][a] = by_norm[key].get(a, 0) + 1
    groups = []
    for variants in by_norm.values():
        if len(variants) > 1:
            groups.append(sorted(variants.items(), key=lambda kv: -kv[1]))
    return sorted(groups, key=lambda g: g[0][0].lower())


def find_moved(missing_path: str, candidates_by_name: dict[str, list[Track]], size: int = 0) -> str:
    """Caută o piesă dispărută după numele fișierului (și mărime, dacă se știe)."""
    name = os.path.basename(missing_path).lower()
    cands = candidates_by_name.get(name, [])
    if size:
        same = [c for c in cands if c.size == size]
        if same:
            return same[0].path
    return cands[0].path if len(cands) == 1 else ""
