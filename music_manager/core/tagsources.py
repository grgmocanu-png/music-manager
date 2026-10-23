"""Surse noi pentru Taguri online (pe lângă Discogs, Bandcamp, MusicBrainz, Spotify, Last.fm).

Cu API oficial, stabile: Deezer, iTunes / Apple Music, TheAudioDB.
Citind paginile publice (pot să se strice dacă site-ul se schimbă): Beatport, Traxsource, Juno Download,
SoundCloud. Fiecare funcție întoarce un TagResult sau None și nu aruncă excepții.
"""
from __future__ import annotations

import html as htmlmod
import json
import re
import urllib.parse
import urllib.request

from .matching import _ascii, _words, split_title

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")


# --- ajutoare ------------------------------------------------------------------
def get_text(finder, url: str, timeout: int = 20, headers: dict | None = None) -> str:
    h = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
         "Accept-Language": "en-US,en;q=0.8"}
    h.update(headers or {})
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=finder._ctx) as r:
            return r.read(4 * 1024 * 1024).decode("utf-8", "ignore")
    except Exception:
        return ""


def get_json(finder, url: str, headers: dict | None = None):
    txt = get_text(finder, url, headers={"Accept": "application/json", **(headers or {})})
    try:
        return json.loads(txt) if txt else None
    except ValueError:
        return None


def year_of(s) -> str:
    m = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", str(s or ""))
    return m.group(0) if m else ""


def title_ok(wanted: str, found: str) -> bool:
    """Titlul găsit e aceeași piesă? (miezul titlului, fără remix / feat.; tolerant la ordine)."""
    a, _va = split_title(wanted)
    b, _vb = split_title(found)
    a, b = _words(a), _words(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    wa, wb = set(a.split()), set(b.split())
    return len(wa & wb) / max(1, len(wa | wb)) >= 0.6


def version_ok(wanted: str, found: str) -> bool:
    """Dacă ai cerut un remix anume, nu luăm originalul (și invers) când versiunea e scrisă clar."""
    _a, va = split_title(wanted)
    _b, vb = split_title(found)
    if not va or not vb:
        return True
    wa, wb = set(_words(va).split()) - {"remix", "mix", "edit"}, set(_words(vb).split()) - {"remix", "mix", "edit"}
    return not wa or not wb or bool(wa & wb)


def dur_ok(wanted: float, found: float, tol: float = 20) -> bool:
    return not wanted or not found or abs(wanted - found) <= tol


def _pick(finder, artist, title, duration, cands):
    """cands: [(artist, titlu, durată, obiect)] → obiectul cel mai potrivit sau None."""
    best, best_sc = None, -1.0
    for a, t, d, obj in cands:
        if not finder._artist_ok(artist, a) or not title_ok(title, t):
            continue
        sc = 1.0
        if version_ok(title, t):
            sc += 1.0
        if dur_ok(duration, d, 6):
            sc += 1.0
        elif not dur_ok(duration, d, 30):
            continue
        if sc > best_sc:
            best, best_sc = obj, sc
    return best


def _styles(finder, names) -> list[str]:
    out = []
    for n in names:
        n = (n or "").strip()
        if n and n.lower() not in (x.lower() for x in out):
            out.append(n)
    return out


def _result(finder, source, styles, raw=(), year="", label="", cover="", match="", only_genre=False):
    from .tagsearch import TagResult
    styles = _styles(finder, styles)
    res = TagResult(styles=styles, raw_genres=list(raw), year=year, label=label, cover_url=cover or "",
                    source=source, match=match, only_genre=only_genre)
    res.genre = finder.mapper.classify(styles, list(raw))
    return None if res.empty() else res


def _q(artist, title) -> str:
    core = split_title(title)[0]
    return f"{artist.split(',')[0].strip()} {core}".strip()


# --- Deezer (API oficial, fără cont) -------------------------------------------
def deezer(finder, artist, title, duration=0):
    q = f'artist:"{artist.split(",")[0].strip()}" track:"{split_title(title)[0]}"'
    data = get_json(finder, "https://api.deezer.com/search?" + urllib.parse.urlencode({"q": q, "limit": 15}))
    items = (data or {}).get("data") or []
    if not items:
        data = get_json(finder, "https://api.deezer.com/search?" + urllib.parse.urlencode({"q": _q(artist, title)}))
        items = (data or {}).get("data") or []
    hit = _pick(finder, artist, title, duration,
                [((d.get("artist") or {}).get("name", ""), d.get("title", ""), float(d.get("duration") or 0), d)
                 for d in items])
    if not hit:
        return None
    alb_id = (hit.get("album") or {}).get("id")
    alb = get_json(finder, f"https://api.deezer.com/album/{alb_id}") if alb_id else None
    alb = alb or {}
    genres = [g.get("name", "") for g in ((alb.get("genres") or {}).get("data") or [])]
    cover = (hit.get("album") or {}).get("cover_xl") or alb.get("cover_xl") or ""
    return _result(finder, "Deezer", genres, (), year_of(alb.get("release_date")), alb.get("label", "") or "",
                   cover, f"{(hit.get('artist') or {}).get('name', '')} - {hit.get('title', '')}")


# --- iTunes / Apple Music (API oficial, fără cont) -----------------------------
def itunes(finder, artist, title, duration=0):
    data = get_json(finder, "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {"term": _q(artist, title), "entity": "song", "limit": 15}))
    items = (data or {}).get("results") or []
    hit = _pick(finder, artist, title, duration,
                [(d.get("artistName", ""), d.get("trackName", ""), float(d.get("trackTimeMillis") or 0) / 1000, d)
                 for d in items])
    if not hit:
        return None
    cover = (hit.get("artworkUrl100") or "").replace("100x100bb", "600x600bb")
    g = hit.get("primaryGenreName", "")
    return _result(finder, "iTunes", [g] if g else [], (), year_of(hit.get("releaseDate")), "", cover,
                   f"{hit.get('artistName', '')} - {hit.get('trackName', '')}")


# --- TheAudioDB (API gratuit) ------------------------------------------------------
def theaudiodb(finder, artist, title, duration=0):
    for key in ("123", "2"):
        data = get_json(finder, f"https://www.theaudiodb.com/api/v1/json/{key}/searchtrack.php?" +
                        urllib.parse.urlencode({"s": artist.split(",")[0].strip(), "t": split_title(title)[0]}))
        items = (data or {}).get("track") or []
        if items:
            break
    else:
        return None
    hit = _pick(finder, artist, title, duration,
                [(d.get("strArtist", ""), d.get("strTrack", ""), float(d.get("intDuration") or 0) / 1000, d)
                 for d in items])
    if not hit:
        return None
    styles = [x for x in (hit.get("strStyle"), hit.get("strGenre")) if x]
    return _result(finder, "TheAudioDB", styles, (), "", "", hit.get("strTrackThumb") or "",
                   f"{hit.get('strArtist', '')} - {hit.get('strTrack', '')}")


# --- Beatport (pagina publică de căutare) -------------------------------------------
def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def parse_beatport(page: str) -> list[dict]:
    """Piesele din pagina de căutare Beatport (datele din __NEXT_DATA__)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    out = []
    for d in _walk(data):
        name = d.get("track_name") or (d.get("name") if "mix_name" in d else None)
        if not name or not isinstance(d.get("artists"), list):
            continue
        gen = d.get("genre")
        if isinstance(gen, list):
            gen = gen[0] if gen else {}
        sub = d.get("sub_genre") or {}
        if isinstance(sub, list):
            sub = sub[0] if sub else {}
        rel = d.get("release") or {}
        lab = d.get("label") or rel.get("label") or {}
        out.append({
            "title": f"{name} ({d.get('mix_name')})" if d.get("mix_name") else name,
            "artist": ", ".join(a.get("artist_name") or a.get("name") or "" for a in d["artists"] if isinstance(a, dict)),
            "genre": (gen or {}).get("genre_name") or (gen or {}).get("name") or "",
            "sub": (sub or {}).get("sub_genre_name") or (sub or {}).get("name") or "",
            "label": (lab or {}).get("label_name") or (lab or {}).get("name") or "",
            "date": d.get("publish_date") or d.get("release_date") or rel.get("release_date") or "",
            "length": float(d.get("length") or d.get("length_ms") or 0) / 1000,
            "image": rel.get("release_image_uri") or (rel.get("image") or {}).get("uri", "") or "",
            "bpm": d.get("bpm") or "", "key": d.get("key_name") or "",
        })
    return out


def beatport(finder, artist, title, duration=0):
    page = get_text(finder, "https://www.beatport.com/search/tracks?" + urllib.parse.urlencode({"q": _q(artist, title)}))
    items = parse_beatport(page)
    hit = _pick(finder, artist, title, duration, [(d["artist"], d["title"], d["length"], d) for d in items])
    if not hit:
        return None
    styles = [x for x in (hit["sub"], hit["genre"]) if x]
    return _result(finder, "Beatport", styles, (), year_of(hit["date"]), hit["label"], hit["image"],
                   f"{hit['artist']} - {hit['title']}")


# --- Traxsource și Juno (pagini publice) ------------------------------------------
def _txt(s: str) -> str:
    return htmlmod.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def parse_traxsource(page: str) -> list[dict]:
    out = []
    for blk in re.findall(r'<div[^>]+class="[^"]*\btrk-row\b[^"]*"[^>]*>(.*?)(?=<div[^>]+class="[^"]*\btrk-row\b|$)',
                          page or "", re.S):
        def cls(name):
            m = re.search(r'class="[^"]*\b' + name + r'\b[^"]*"[^>]*>(.*?)</div>', blk, re.S)
            return re.sub(r"\s+", " ", _txt(m.group(1))) if m else ""
        title = cls("title")
        if not title:
            continue
        dur = 0.0
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", cls("duration") or title)
        if m:
            dur = int(m.group(1)) * 60 + int(m.group(2))
        img = re.search(r'<img[^>]+src="([^"]+)"', blk)
        out.append({"title": re.sub(r"\s*\d{1,2}:\d{2}\s*$", "", title), "artist": cls("com-artists"),
                    "label": cls("com-label"), "genre": cls("genre"), "date": cls("r-date"), "length": dur,
                    "image": img.group(1) if img else ""})
    return out


def traxsource(finder, artist, title, duration=0):
    page = get_text(finder, "https://www.traxsource.com/search/tracks?" + urllib.parse.urlencode({"term": _q(artist, title)}))
    items = parse_traxsource(page)
    hit = _pick(finder, artist, title, duration, [(d["artist"], d["title"], d["length"], d) for d in items])
    if not hit or not (hit["genre"] or hit["label"]):
        return None
    return _result(finder, "Traxsource", [hit["genre"]] if hit["genre"] else [], (), year_of(hit["date"]),
                   hit["label"], hit["image"], f"{hit['artist']} - {hit['title']}")


def parse_juno(page: str) -> list[dict]:
    out = []
    for blk in re.findall(r'<div[^>]+class="[^"]*\bproduct-list-item\b[^"]*"[^>]*>(.*?)(?=<div[^>]+class="[^"]*'
                          r'\bproduct-list-item\b|$)', page or "", re.S):
        artist = _txt(" ".join(re.findall(r'<a[^>]+href="/artists/[^"]*"[^>]*>(.*?)</a>', blk, re.S)))
        title = _txt((re.search(r'<a[^>]+href="/products/[^"]*"[^>]*>(.*?)</a>', blk, re.S) or [None, ""])[1])
        label = _txt((re.search(r'<a[^>]+href="/labels/[^"]*"[^>]*>(.*?)</a>', blk, re.S) or [None, ""])[1])
        genre = ""
        m = re.search(r'Genre:\s*</?[^>]*>?\s*([^<]+)<', blk) or re.search(r'class="[^"]*genre[^"]*"[^>]*>(.*?)<',
                                                                           blk, re.S)
        if m:
            genre = _txt(m.group(1))
        date = (re.search(r"\b(\d{1,2} [A-Z][a-z]{2} \d{2,4})\b", blk) or [None, ""])[1]
        img = re.search(r'<img[^>]+(?:data-src|src)="([^"]+\.jpg)"', blk)
        if artist and title:
            out.append({"artist": artist, "title": title, "label": label, "genre": genre,
                        "date": date, "length": 0.0, "image": img.group(1) if img else ""})
    return out


def juno(finder, artist, title, duration=0):
    page = get_text(finder, "https://www.junodownload.com/search/?" + urllib.parse.urlencode(
        {"q[all][]": _q(artist, title), "solrorder": "relevancy"}))
    items = parse_juno(page)
    hit = _pick(finder, artist, title, 0, [(d["artist"], d["title"], 0, d) for d in items])
    if not hit or not (hit["genre"] or hit["label"]):
        return None
    year = year_of(hit["date"]) or (("20" + hit["date"][-2:]) if re.search(r"\d{2}$", hit["date"]) else "")
    return _result(finder, "Juno Download", [hit["genre"]] if hit["genre"] else [], (), year, hit["label"],
                   hit["image"], f"{hit['artist']} - {hit['title']}")


# --- SoundCloud (API-ul public al site-ului; genul e scris de cine urcă) --------------
_SC_ID: dict = {}


def _sc_client_id(finder) -> str:
    if "id" in _SC_ID:
        return _SC_ID["id"]
    cid = ""
    home = get_text(finder, "https://soundcloud.com/")
    for src in reversed(re.findall(r'<script[^>]+src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', home)):
        js = get_text(finder, src)
        m = re.search(r'client_id\s*[:=]\s*"([A-Za-z0-9]{20,})"', js)
        if m:
            cid = m.group(1)
            break
    _SC_ID["id"] = cid
    return cid


def soundcloud(finder, artist, title, duration=0):
    cid = _sc_client_id(finder)
    if not cid:
        return None
    data = get_json(finder, "https://api-v2.soundcloud.com/search/tracks?" + urllib.parse.urlencode(
        {"q": _q(artist, title), "client_id": cid, "limit": 15}))
    items = (data or {}).get("collection") or []
    cands = []
    for d in items:
        user = (d.get("user") or {}).get("username", "")
        pub = d.get("publisher_metadata") or {}
        a = pub.get("artist") or user
        t = d.get("title", "")
        if " - " in t and not finder._artist_ok(artist, a):      # „Artist - Titlu” în titlu, urcat de un label
            a, t = t.split(" - ", 1)
        cands.append((a, t, float(d.get("duration") or d.get("full_duration") or 0) / 1000, d))
    hit = _pick(finder, artist, title, duration, cands)
    if not hit:
        return None
    tags = re.findall(r'"([^"]+)"|(\S+)', hit.get("tag_list") or "")
    names = [hit.get("genre") or ""] + [a or b for a, b in tags]
    known = [n for n in names if n and finder.mapper.family_of(n)]
    if not known:
        return None
    return _result(finder, "SoundCloud", known[:4], (), "", "", "", f"{hit.get('title', '')}", only_genre=True)


# --- registru -------------------------------------------------------------------------
SOURCES = ["din fișier", "Discogs", "Bandcamp", "Beatport", "Traxsource", "Juno Download", "Deezer", "iTunes",
           "TheAudioDB", "MusicBrainz", "Spotify", "Last.fm", "SoundCloud", "Discogs după artist"]
SCRAPED = {"Beatport", "Traxsource", "Juno Download", "SoundCloud"}
GENRE_ONLY = {"din fișier", "Last.fm", "Discogs după artist", "SoundCloud"}
NOTES = {
    "din fișier": "Mood / Grouping / STYLE din fișierul tău",
    "Discogs": "cel mai bun pentru electronică (cere token)",
    "Bandcamp": "doar când piesa are link de Bandcamp în taguri",
    "Beatport": "genuri detaliate (citește site-ul)",
    "Traxsource": "house, deep, afro, soulful (citește site-ul)",
    "Juno Download": "techno, DnB, breaks (citește site-ul)",
    "Deezer": "API oficial: gen pe album, an, label, copertă",
    "iTunes": "API oficial: gen principal, an, copertă",
    "TheAudioDB": "API gratuit: gen și stil",
    "MusicBrainz": "fără cont: an, copertă, taguri",
    "Spotify": "genul artistului (cere conectare)",
    "Last.fm": "tagurile ascultătorilor (cere cheie)",
    "SoundCloud": "genul scris de cine urcă (doar dacă e un gen cunoscut)",
    "Discogs după artist": "ultima variantă: genul cel mai frecvent al artistului",
}


def configured_sources(raw: str) -> list[tuple[str, bool]]:
    """Ordinea și bifele din Setări (JSON [[nume, bifat], …]); sursele noi se adaugă la locul lor implicit."""
    try:
        lst = [(str(n), bool(on)) for n, on in json.loads(raw)] if raw else []
    except (ValueError, TypeError):
        lst = []
    names = [n for n, _ in lst if n in SOURCES]
    out = [(n, on) for n, on in lst if n in SOURCES]
    for i, n in enumerate(SOURCES):
        if n not in names:
            out.insert(min(i, len(out)), (n, True))
    return out
