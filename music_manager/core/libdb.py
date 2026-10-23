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

from .safeio import write_json
from .djcollections import Collection, best_values, norm_path
from .library import Track, normalize
from .playlist import compatible_keys, to_camelot

DEFAULT_PRIORITY = ["Traktor", "Rekordbox", "VirtualDJ", "Fișier", "Analiză"]

# rolul piesei într-un set
ROLES = ["Opener", "Warm-up", "Build", "Peak", "Closer", "After"]
_ROLE_BY_LOWER = {r.lower(): r for r in ROLES}
_ROLE_BY_LOWER.update({"warmup": "Warm-up", "warm up": "Warm-up", "peak time": "Peak", "peaktime": "Peak",
                       "afterhours": "After", "after hours": "After", "intro": "Opener", "outro": "Closer"})
_JUNK_COMMENT_RE = re.compile(r"^\s*(\d{1,2}\s*[-.]?\s*)?danceability(\s*[:=]?\s*\d{1,2})?\s*$", re.I)
_ENERGY_RE = re.compile(r"^(?:e|energy|energie)\s*:?\s*(10|[1-9])$", re.I)


def parse_comment(comment: str) -> tuple[str, str, int]:
    """„Deep House | Peak | E7” -> („Deep House”, „Peak”, 7). Restul textului rămâne stil."""
    style_parts, role, energy = [], "", 0
    for part in [p.strip() for p in (comment or "").split("|")]:
        if not part:
            continue
        low = part.lower()
        if low in _ROLE_BY_LOWER and not role:
            role = _ROLE_BY_LOWER[low]
            continue
        if _JUNK_COMMENT_RE.match(part):
            continue                      # „02 Danceability” (pus de alt program) — îl ignorăm
        m = _ENERGY_RE.match(part)
        if m and not energy:
            energy = int(m.group(1))
            continue
        style_parts.append(part)
    return " | ".join(style_parts), role, energy


def has_junk_comment(comment: str) -> bool:
    return any(_JUNK_COMMENT_RE.match(p) for p in (comment or "").split("|"))


def compose_comment(style: str, role: str = "", energy: int = 0) -> str:
    parts = [style.strip()] if style and style.strip() else []
    if role:
        parts.append(role)
    if energy:
        parts.append(f"E{energy}")
    return " | ".join(parts)


def suggest_role(energy: int, bpm: float, plays: int = 0, avg_pos: float = -1) -> str:
    """Rol propus: din seturile tale (dacă ai cântat piesa de măcar 2 ori), altfel din energie."""
    if plays >= 2 and avg_pos >= 0:
        for limit, role in ((0.2, "Opener"), (0.4, "Warm-up"), (0.65, "Build"), (0.88, "Peak")):
            if avg_pos < limit:
                return role
        return "Closer"
    if not energy:
        return ""
    if bpm and bpm < 105 and energy <= 4:
        return "After"
    if energy <= 3:
        return "Opener"
    if energy <= 5:
        return "Warm-up"
    if energy == 6:
        return "Build"
    return "Peak"


def parse_traktor_history(folder: str, min_tracks: int = 8) -> dict:
    """Seturile tale din Traktor (History): pentru fiecare piesă, de câte ori și unde în set a fost cântată.

    Întoarce {cale normalizată: [poziții relative 0..1]}.
    """
    from .djcollections import parse_traktor
    out: dict[str, list] = {}
    if not folder or not os.path.isdir(folder):
        return out
    for f in sorted(os.listdir(folder)):
        if not f.lower().endswith(".nml"):
            continue
        col = parse_traktor(os.path.join(folder, f))
        if col.error:
            continue
        for paths in col.playlists.values():
            seq = list(dict.fromkeys(paths))
            if len(seq) < min_tracks:
                continue
            for i, p in enumerate(seq):
                out.setdefault(p, []).append(i / (len(seq) - 1))
    return out


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
    an_energy: int = 0
    role: str = ""                # rolul pus de tine
    an_date: str = ""             # când a fost analizată
    an_error: str = ""            # analiza a eșuat (nu se mai reîncearcă singură)
    user_energy: int = 0          # energie pusă de tine (are prioritate)
    user_family: str = ""         # familie pusă de tine (are prioritate față de cea din gen)


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
    family: str = ""
    playlists: list = field(default_factory=list)
    drops: list = field(default_factory=list)
    missing: bool = False
    offline: bool = False
    energy: int = 0
    energy_src: str = ""
    role: str = ""
    role_suggested: str = ""
    plays: int = 0
    avg_pos: float = -1
    analyzed: str = ""
    an_error: str = ""
    bpm_suggest: float = 0.0      # BPM-ul pare greșit pentru familie: dublul / jumătatea s-ar potrivi

    @property
    def path(self) -> str:
        return self.track.path

    @property
    def camelot(self) -> str:
        return to_camelot(self.key)

    @property
    def style(self) -> str:
        return parse_comment(self.track.comment)[0]

    @property
    def role_or_suggestion(self) -> str:
        return self.role or self.role_suggested


class LibraryDB:
    def __init__(self, path: str):
        self.path = path
        self.extras: dict[str, Extra] = {}
        self.playlists: list[Playlist] = []
        self.relocations: dict[str, str] = {}      # cale veche (normalizată) -> cale nouă
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in (data.get("extras") or {}).items():
                try:
                    self.extras[k] = Extra(**v)
                except TypeError:
                    pass
            self.relocations = dict(data.get("relocations") or {})
            for p in data.get("playlists") or []:
                try:
                    self.playlists.append(Playlist(**p))
                except TypeError:
                    pass
        except (OSError, ValueError):
            pass

    def save(self):
        write_json(self.path, {"extras": {k: asdict(v) for k, v in self.extras.items()},
                               "playlists": [asdict(p) for p in self.playlists],
                               "relocations": self.relocations}, ensure_ascii=False)

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
                priority: list[str] | None = None, history: dict | None = None) -> list[LibItem]:
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
                     rating=e.rating or dj.rating or getattr(t, "rating", 0), color=e.color or dj.color, date_added=e.date_added,
                     in_traktor=bool(traktor and traktor.lookup(t.path) is not None), drops=e.an_drops)
        _, c_role, c_energy = parse_comment(t.comment)
        it.energy, it.energy_src = (c_energy, "Comentariu") if c_energy else (e.an_energy, "Analiză" if e.an_energy else "")
        if e.user_energy:
            it.energy, it.energy_src = e.user_energy, "Manual"
        it.role = e.role or c_role
        it.analyzed, it.an_error = e.an_date, e.an_error
        pos = (history or {}).get(norm_path(t.path)) or []
        it.plays = len(pos)
        it.avg_pos = sum(pos) / len(pos) if pos else -1
        if not it.role:
            it.role_suggested = suggest_role(it.energy, it.bpm, it.plays, it.avg_pos)
        items.append(it)
    return items


# --- reguli pentru playlisturi inteligente -----------------------------------
RULE_FIELDS = {
    "family": "Familie de gen", "genre": "Gen", "style": "Stil / comentariu", "playlist": "E în playlistul", "bpm": "BPM", "key": "Tonalitate", "year": "An",
    "rating": "Rating (stele)", "added": "Adăugată", "format": "Format", "artist": "Artist",
    "title": "Titlu", "label": "Label", "folder": "Folder", "in_traktor": "E în Traktor",
    "has_cover": "Are copertă", "role": "Rol în set", "energy": "Energie (1–10)", "plays": "Cântată în seturile mele",
}
RULE_OPS = {
    "family": ["este oricare din", "nu este"], "playlist": ["conține"],
    "genre": ["este oricare din", "nu este"], "style": ["conține", "nu conține"],
    "bpm": ["între"], "key": ["este", "compatibilă cu"], "year": ["între"], "rating": ["cel puțin"],
    "added": ["în ultimele N zile"], "format": ["este oricare din"], "artist": ["conține", "nu conține"],
    "title": ["conține", "nu conține"], "label": ["conține"], "folder": ["conține"],
    "in_traktor": ["da", "nu"], "has_cover": ["da", "nu"],
    "role": ["este oricare din", "este oricare din (inclusiv sugestii)"], "energy": ["între"],
    "plays": ["de cel puțin N ori", "niciodată"],
}


def _bpm_in(b: float, lo: float, hi: float, half_double: bool = False) -> bool:
    """BPM-ul în interval. Fără dublare/înjumătățire (doar „piese care se mixează” o folosește)."""
    if not b:
        return False
    vals = (b, b * 2, b / 2) if half_double else (b,)
    return any((not lo or x >= lo) and (not hi or x <= hi) for x in vals)


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
    if f == "family":
        hit = normalize(it.family) in {normalize(g) for g in _list(v)}
        return hit if op != "nu este" else not hit
    if f == "playlist":
        return any(normalize(str(v)) in normalize(p) for p in it.playlists)
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
    if f == "role":
        wanted = {_ROLE_BY_LOWER.get(x.lower(), x) for x in _list(v)}
        role = it.role_or_suggestion if "sugestii" in op else it.role
        return role in wanted
    if f == "energy":
        lo, hi = (int(_f(x)) for x in (list(v) + [0, 0])[:2]) if isinstance(v, list) else (int(_f(v)), 0)
        if not it.energy:
            return False
        return (not lo or it.energy >= lo) and (not hi or it.energy <= hi)
    if f == "plays":
        return it.plays == 0 if op == "niciodată" else it.plays >= int(_f(v) or 1)
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


def _traktor_loc(path: str, mac_volume: str = "Macintosh HD") -> tuple[str, str, str]:
    p = os.path.abspath(path).replace("\\", "/")
    vol = ""
    if not (len(p) > 1 and p[1] == ":"):
        vol = mac_volume        # pe Mac, Traktor ține numele discului (ex. „Macintosh HD”)
        if p.startswith("/Volumes/"):            # disc extern: /Volumes/NUME/...
            parts = p.split("/", 3)
            vol = parts[2]
            p = "/" + (parts[3] if len(parts) > 3 else "")
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
        w.writerow(["Artist", "Titlu", "Gen", "Stil", "Rol", "Energie", "BPM", "Tonalitate", "Camelot", "An",
                    "Label", "Rating", "Cale"])
        for it in items:
            t = it.track
            w.writerow([t.artist, t.display_title, t.genre, it.style, it.role, it.energy or "", it.bpm or "",
                        it.key, it.camelot, t.year, t.label, it.rating or "", it.path])


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


def relocate_by_name(missing: list[str], folder: str) -> dict[str, str]:
    """Caută fișierele lipsă într-un folder (cu subfoldere) după nume. Întoarce {cale veche: cale nouă}."""
    from .library import find_audio_files
    index: dict[str, list[str]] = {}
    for f in find_audio_files([folder]):
        index.setdefault(os.path.basename(f).lower(), []).append(f)
    out = {}
    for m in missing:
        cands = index.get(os.path.basename(m.replace("\\", "/")).lower(), [])
        if len(cands) == 1:
            out[m] = cands[0]
        elif len(cands) > 1:
            # mai multe cu același nume: alegem pe cel cu cele mai multe foldere comune în cale
            parts = set(m.replace("\\", "/").lower().split("/"))
            out[m] = max(cands, key=lambda c: len(parts & set(c.replace("\\", "/").lower().split("/"))))
    return out


_LEAD_NUM = re.compile(r"^\s*(?:(?:cd|disc)\s*\d+\s*[-._]?\s*)?[a-d]?\d{1,2}(?:\s*[.)\]_\-–]+\s*|\s+)", re.I)
_MID_NUM = re.compile(r"\s[-–]\s*\d{1,2}(?:\s*[.)\]_\-–]+\s*|\s+)")


def clean_lookup_name(name: str) -> str:
    """Numele unei piese lipsă, curățat pentru căutare: fără folder, extensie și număr de piesă.
    „04. the limiñanas - que calor !” → „the limiñanas - que calor !”; „blue pearl - 01 little brother”
    → „blue pearl - little brother”."""
    base = os.path.basename((name or "").replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    if ext.lower().lstrip(".") in ("mp3", "flac", "wav", "aiff", "aif", "m4a", "ogg", "opus", "wma", "alac", "aac"):
        base = stem
    base = base.replace("_", " ")
    base = _LEAD_NUM.sub("", base, count=1)
    base = _MID_NUM.sub(" - ", base)
    return " ".join(base.split()).strip(" -.")


def name_index(items: list) -> list:
    """Cuvintele fiecărei piese (artist + titlu + numele fișierului), calculate o singură dată."""
    out = []
    for it in items:
        if getattr(it, "missing", False) or getattr(it, "offline", False):
            continue
        t = it.track
        out.append((it, set(normalize(f"{t.artist} {t.title} {os.path.splitext(os.path.basename(t.path))[0]}").split())))
    return out


def find_by_name(query: str, items: list, duration: float = 0, limit: int = 30,
                 index: list | None = None) -> list[tuple[float, "LibItem"]]:
    """Piesele din bibliotecă ce se potrivesc cu un nume (cuvintele căutării regăsite în
    artist + titlu + numele fișierului). Întoarce [(scor 0..1, piesă)], cele mai bune primele."""
    q = set(normalize(clean_lookup_name(query) if os.sep in query or "/" in query else query).split())
    q = {w for w in q if w not in ("feat", "ft", "and", "original", "mix", "remix") or len(q) <= 2}
    if not q:
        return []
    out = []
    for it, hay in (index if index is not None else name_index(items)):
        t = it.track
        hit = len(q & hay) / len(q)
        if hit < 0.5:
            continue
        score = hit
        if duration and t.duration:
            score += 0.2 if abs(t.duration - duration) < 4 else -0.2
        out.append((score, it))
    out.sort(key=lambda x: -x[0])
    return out[:limit]


def auto_match_by_name(query: str, items: list, duration: float = 0, index: list | None = None):
    """O singură potrivire sigură (toate cuvintele, minim 2, fără egal) — altfel None."""
    words = normalize(clean_lookup_name(query)).split()
    if len(words) < 2:
        return None
    res = find_by_name(clean_lookup_name(query), items, duration, limit=3, index=index)
    full = [(sc, it) for sc, it in res if sc >= 1.0 - 1e-9]
    if not full:
        return None
    if len(full) > 1 and full[1][0] >= full[0][0] - 1e-9:
        return None
    return full[0][1]


def playlist_profile(items: list[LibItem]) -> tuple[str, float, str]:
    """Familia dominantă, BPM-ul median și rolul dominant al unui playlist."""
    def top(values):
        c: dict[str, int] = {}
        for v in values:
            if v:
                c[v] = c.get(v, 0) + 1
        return max(c.items(), key=lambda kv: kv[1])[0] if c else ""
    bpms = sorted(it.bpm for it in items if it.bpm)
    return (top(it.family for it in items), (bpms[len(bpms) // 2] if bpms else 0.0),
            top(it.role for it in items))


# intervale tipice pe familie — folosite DOAR ca să marcheze „BPM de verificat”, niciodată la playlisturi
DEFAULT_BPM_RANGES = {
    "House": (115, 130), "Techno": (125, 150), "Disco": (110, 128), "Funk & Soul": (90, 125),
    "Drum & Bass": (160, 180), "Hip-Hop": (80, 100), "Electro": (115, 140), "Trance": (128, 145),
    "Breaks": (120, 140),
}


def bpm_suggestion(bpm: float, family: str, ranges: dict | None = None) -> float:
    """Dacă BPM-ul e în afara intervalului familiei, dar dublul sau jumătatea intră: acela. Altfel 0."""
    ranges = DEFAULT_BPM_RANGES if ranges is None else ranges
    if not bpm or not family or family not in ranges:
        return 0.0
    lo, hi = ranges[family]
    if lo <= bpm <= hi:
        return 0.0
    for cand in (bpm * 2, bpm / 2):
        if lo <= cand <= hi:
            return round(cand, 2)
    return 0.0


def traktor_snapshot(collections) -> dict:
    """BPM / Key / Rating din colecția Traktor, pe cale: {cale: [bpm, key, rating]}."""
    tk = next((c for c in collections if c.source == "Traktor" and not c.error), None)
    if tk is None:
        return {}
    return {k: [round(float(v.bpm or 0), 2), v.key or "", int(v.rating or 0)] for k, v in tk.tracks.items()}


def diff_snapshot(old: dict, new: dict) -> list[tuple[str, str, object, object]]:
    """[(cale, câmp, vechi, nou)] pentru piesele schimbate în Traktor de la ultima încărcare."""
    out = []
    if not old:
        return out
    names = ("bpm", "key", "rating")
    for k, vals in new.items():
        prev = old.get(k)
        if prev is None:
            continue
        for i, n in enumerate(names):
            a, b = prev[i], vals[i]
            if n == "bpm":
                if a and b and abs(float(a) - float(b)) < 0.05:
                    continue
                if not b:
                    continue
            if a != b and not (n != "bpm" and not b and not a):
                out.append((k, n, a, b))
    return out


def top_value(items: list, fn) -> str:
    """Valoarea cea mai frecventă (nevidă) a unei coloane într-un playlist."""
    c: dict[str, int] = {}
    for it in items:
        v = fn(it)
        if v:
            c[v] = c.get(v, 0) + 1
    return max(c.items(), key=lambda kv: kv[1])[0] if c else ""


def bpm_bucket(bpm: float, width: int = 10) -> tuple[int, int]:
    lo = int(bpm // width * width)
    return lo, lo + width


def find_moved(missing_path: str, candidates_by_name: dict[str, list[Track]], size: int = 0) -> str:
    """Caută o piesă dispărută după numele fișierului (și mărime, dacă se știe)."""
    name = os.path.basename(missing_path).lower()
    cands = candidates_by_name.get(name, [])
    if size:
        same = [c for c in cands if c.size == size]
        if same:
            return same[0].path
    return cands[0].path if len(cands) == 1 else ""
