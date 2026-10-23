"""Găsește taguri lipsă (gen, stil, an, label, copertă) online.

Ordinea surselor:
  1. din fișier: Mood / Grouping / STYLE (dacă arată a gen)
  2. Discogs (cere token personal gratuit) — tolerant: fără remix, fără feat., fără „Pt. 2”
  3. Bandcamp — doar dacă piesa are în taguri un link de Bandcamp
  4. MusicBrainz + Cover Art Archive (fără cont)
  5. Spotify (genul artistului)
  6. Last.fm (cere cheie API gratuită)
  7. Discogs după artist — genul/stilul cel mai frecvent al artistului (ultima variantă)
Doar citim informații publice; nu se modifică nimic în conturile tale.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .. import __version__
from .genres import GenreMapper
from .matching import _ascii, _words, artist_tokens, split_title

USER_AGENT = f"MusicManager/{__version__} (+https://github.com/grgmocanu-png/music-manager)"
MAX_COVER_BYTES = 4 * 1024 * 1024


def _ctx():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class _Throttle:
    def __init__(self, interval: float):
        self.interval = interval
        self.last = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            d = self.last + self.interval - time.time()
            if d > 0:
                time.sleep(d)
            self.last = time.time()


@dataclass
class TagResult:
    genre: str = ""               # familia (ex. House)
    styles: list = field(default_factory=list)
    raw_genres: list = field(default_factory=list)
    year: str = ""
    label: str = ""
    cover_url: str = ""
    cover_urls: list = field(default_factory=list)   # toate copertele găsite, în ordinea surselor
    source: str = ""              # Discogs / MusicBrainz / Spotify (pe câmp: vezi sources)
    sources: dict = field(default_factory=dict)
    match: str = ""               # ce a găsit (pentru verificare)
    reasons: list = field(default_factory=list)       # de ce n-a găsit (pe surse)
    only_genre: bool = False      # rezultat aproximativ: folosim doar genul / stilul

    @property
    def style_text(self) -> str:
        return ", ".join(self.styles[:3])

    def empty(self) -> bool:
        return not (self.genre or self.styles or self.year or self.label or self.cover_url)


class TagFinder:
    def __init__(self, discogs_token: str = "", spotify=None, mapper: GenreMapper | None = None,
                 use_musicbrainz: bool = True, original_release: bool = True, lastfm_key: str = "",
                 use_bandcamp: bool = True, by_artist: bool = True):
        self.token = (discogs_token or "").strip()
        self.lastfm_key = (lastfm_key or "").strip()
        self.use_bandcamp = use_bandcamp
        self.by_artist = by_artist
        self._lastfm_t = _Throttle(0.25)
        self._bc_t = _Throttle(1.0)
        self._artist_cache: dict[str, TagResult | None] = {}
        self.spotify = spotify
        self.mapper = mapper or GenreMapper()
        self.use_mb = use_musicbrainz
        self.original = original_release
        self._ctx = _ctx()
        self._discogs_t = _Throttle(1.1)    # 60 cereri / minut
        self._mb_t = _Throttle(1.1)         # 1 cerere / secundă

    # --- HTTP -------------------------------------------------------------
    def _get_json(self, url: str, headers: dict | None = None, retries: int = 3):
        h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        h.update(headers or {})
        for attempt in range(retries):
            req = urllib.request.Request(url, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=20, context=self._ctx) as r:
                    return json.loads(r.read().decode("utf-8", "ignore"))
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 + attempt * 3)
                    continue
                if e.code == 401:
                    raise RuntimeError("Discogs a refuzat tokenul (401). Verifică tokenul din Setări.")
                return None
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(1 + attempt)
        return None

    def download_cover(self, url) -> tuple[bytes, str] | None:
        """Descarcă coperta și o face JPEG simplu ≤600 px (ce citesc sigur Rekordbox / CDJ / Traktor).
        `url` poate fi și o listă: se încearcă pe rând până merge una."""
        from .covers import normalize_cover
        for u in ([url] if isinstance(url, str) else list(url or [])):
            if not u:
                continue
            req = urllib.request.Request(u, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=25, context=self._ctx) as r:
                    data = r.read(MAX_COVER_BYTES + 1)
            except Exception:
                continue
            if not data or len(data) > MAX_COVER_BYTES:
                continue
            out = normalize_cover(data)
            if out:
                return out
        return None

    # --- potrivire ----------------------------------------------------------
    @staticmethod
    def _artist_ok(wanted: str, found: str) -> bool:
        a, b = artist_tokens(wanted), artist_tokens(found)
        if not a or not b:
            return False
        return bool(a & b) or any(x in _words(_ascii(found)) for x in a)

    # --- Discogs -------------------------------------------------------------
    def _discogs_query(self, params: dict) -> list:
        self._discogs_t.wait()
        data = self._get_json("https://api.discogs.com/database/search?" + urllib.parse.urlencode(params),
                              {"Authorization": f"Discogs token={self.token}"})
        return (data or {}).get("results") or []

    def _discogs_result(self, artist: str, results: list, approx: bool) -> TagResult | None:
        good = [r for r in results if self._artist_ok(artist, (r.get("title") or "").split(" - ")[0])]
        if not good:
            return None
        # stilurile: cele mai frecvente în rezultate (mai sigur decât o singură lansare)
        counts: dict[str, int] = {}
        for r in good[:8]:
            for st in r.get("style") or []:
                counts[st] = counts.get(st, 0) + 1
        styles = [s for s, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
        years = sorted(int(r["year"]) for r in good if str(r.get("year", "")).isdigit() and int(r["year"]) > 1900)
        pick = good[0]
        if self.original and years:
            first = years[0]
            pick = next((r for r in good if str(r.get("year")) == str(first)), good[0])
        res = TagResult(styles=styles, raw_genres=list(pick.get("genre") or []), source="Discogs",
                        match=pick.get("title") or "")
        res.genre = self.mapper.classify(styles, res.raw_genres)
        if approx:          # altă lansare a artistului: doar genul e de încredere
            res.only_genre = True
            res.source = "Discogs (aproximativ)"
            return res
        res.year = str(years[0]) if (self.original and years) else str(pick.get("year") or "")
        labels = [l for l in (pick.get("label") or []) if l and "not on label" not in l.lower()]
        res.label = re.sub(r"\s*\(\d+\)$", "", labels[0]) if labels else ""
        cover = pick.get("cover_image") or ""
        if cover and "spacer.gif" not in cover:
            res.cover_url = cover
        return res

    def discogs(self, artist: str, title: str) -> TagResult | None:
        """Încearcă pe rând: titlul întreg → fără remix → fără „feat.” / „Pt. 2” / paranteze,
        pentru fiecare variantă a artistului (ex. „X aka Y” → „X”); apoi artist + un cuvânt rar din titlu."""
        if not self.token:
            return None
        tried = set()
        for a in artist_variants(artist):
            for t in title_variants(title):
                key = (a.lower(), t.lower())
                if key in tried:
                    continue
                tried.add(key)
                results = self._discogs_query({"artist": a, "track": t, "type": "release", "per_page": 15})
                r = self._discogs_result(a, results, approx=False)
                if r:
                    if (a, t) != (artist, title):
                        r.match += f"  [căutat ca: {a} – {t}]"
                    return r
                if len(tried) >= 6:      # destul: nu ținem Discogs ocupat
                    break
        word = rare_word(title)
        if word:
            a = artist_variants(artist)[0]
            results = self._discogs_query({"q": f"{a} {word}", "type": "release", "per_page": 15})
            return self._discogs_result(a, results, approx=True)
        return None

    def discogs_by_artist(self, artist: str) -> TagResult | None:
        """Genul / stilul cel mai frecvent în lansările artistului (când piesa nu se găsește)."""
        if not self.token:
            return None
        a = artist_variants(artist)[0]
        k = a.lower()
        if k in self._artist_cache:
            return self._artist_cache[k]
        results = self._discogs_query({"artist": a, "type": "release", "per_page": 50})
        good = [r for r in results if self._artist_ok(a, (r.get("title") or "").split(" - ")[0])]
        out = None
        if good:
            counts: dict[str, int] = {}
            gcounts: dict[str, int] = {}
            for r in good:
                for st in r.get("style") or []:
                    counts[st] = counts.get(st, 0) + 1
                for g in r.get("genre") or []:
                    gcounts[g] = gcounts.get(g, 0) + 1
            styles = [s for s, _ in sorted(counts.items(), key=lambda kv: -kv[1])]
            raw = [g for g, _ in sorted(gcounts.items(), key=lambda kv: -kv[1])]
            if styles or raw:
                out = TagResult(styles=styles, raw_genres=raw, source="Discogs (după artist)", only_genre=True,
                                match=f"{len(good)} lansări ale lui {a}")
                out.genre = self.mapper.classify(styles, raw)
        self._artist_cache[k] = out
        return out

    # --- Bandcamp (din linkul din taguri) -----------------------------------
    def bandcamp(self, url: str, artist: str = "") -> TagResult | None:
        if not self.use_bandcamp or not url or "bandcamp.com" not in url.lower():
            return None
        if not url.lower().startswith("http"):
            url = "https://" + url.lstrip("/")
        self._bc_t.wait()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 " + USER_AGENT,
                                                   "Accept": "text/html"})
        try:
            with urllib.request.urlopen(req, timeout=20, context=self._ctx) as r:
                html = r.read(3 * 1024 * 1024).decode("utf-8", "ignore")
        except Exception:
            return None
        return parse_bandcamp(html, self.mapper, artist)

    # --- Last.fm -------------------------------------------------------------
    def lastfm(self, artist: str, title: str) -> TagResult | None:
        if not self.lastfm_key:
            return None
        core = title_variants(title)[-1] if title_variants(title) else title
        base = "https://ws.audioscrobbler.com/2.0/?"
        for method, extra in (("track.gettoptags", {"track": core}), ("artist.gettoptags", {})):
            q = {"method": method, "artist": artist_variants(artist)[0], "api_key": self.lastfm_key,
                 "format": "json", "autocorrect": 1, **extra}
            self._lastfm_t.wait()
            data = self._get_json(base + urllib.parse.urlencode(q)) or {}
            tags = ((data.get("toptags") or {}).get("tag")) or []
            if isinstance(tags, dict):
                tags = [tags]
            names = [str(t.get("name") or "").strip() for t in tags if int(t.get("count") or 0) >= 5]
            styles = [n.title() for n in names if self.mapper.family_of(n)][:5]
            if styles:
                res = TagResult(styles=styles, source="Last.fm" if method.startswith("track") else
                                "Last.fm (după artist)", only_genre=not method.startswith("track"),
                                match=", ".join(names[:6]))
                res.genre = self.mapper.classify(styles)
                return res
        return None

    # --- MusicBrainz -------------------------------------------------------
    def musicbrainz(self, artist: str, title: str, duration: float = 0) -> TagResult | None:
        if not self.use_mb:
            return None
        core, _ = split_title(title)
        q = f'recording:"{core}" AND artist:"{artist}"'
        self._mb_t.wait()
        data = self._get_json("https://musicbrainz.org/ws/2/recording/?" +
                              urllib.parse.urlencode({"query": q, "fmt": "json", "limit": 8}))
        recs = (data or {}).get("recordings") or []
        best, best_score = None, -1.0
        for r in recs:
            credit = " ".join(c.get("name", "") for c in (r.get("artist-credit") or []) if isinstance(c, dict))
            if not self._artist_ok(artist, credit):
                continue
            sc = float(r.get("score") or 0)
            if duration and r.get("length"):
                diff = abs(r["length"] / 1000 - duration)
                if diff > 15:
                    continue
                sc += max(0, 10 - diff)
            if sc > best_score:
                best, best_score = r, sc
        if not best:
            return None
        res = TagResult(source="MusicBrainz", match=f"{best.get('title')}")
        frd = best.get("first-release-date") or ""
        if frd[:4].isdigit():
            res.year = frd[:4]
        tags = sorted((t for t in (best.get("tags") or []) if t.get("name")), key=lambda t: -(t.get("count") or 0))
        res.styles = [t["name"].title() for t in tags[:5]]
        res.genre = self.mapper.classify(res.styles)
        releases = best.get("releases") or []
        releases.sort(key=lambda r: r.get("date") or "9999")
        if releases:
            res.cover_url = f"https://coverartarchive.org/release/{releases[0]['id']}/front-500"
        return res

    # --- Spotify -------------------------------------------------------------
    def spotify_lookup(self, artist: str, title: str) -> TagResult | None:
        if not self.spotify or not getattr(self.spotify, "logged_in", False):
            return None
        try:
            found = self.spotify.search_track(artist, split_title(title)[0])
        except Exception:
            return None
        for t in found:
            if self._artist_ok(artist, t.artist):
                res = TagResult(source="Spotify", year=t.year, cover_url=t.cover_url, match=t.label)
                if t.artist_ids:
                    genres = self.spotify.artist_genres(t.artist_ids[0])
                    res.styles = [g.title() for g in genres[:4]]
                    res.genre = self.mapper.classify(res.styles)
                return res
        return None

    # --- tot ---------------------------------------------------------------
    def find(self, artist: str, title: str, duration: float = 0, need=("genre", "year", "label", "cover"),
             hints: dict | None = None) -> TagResult:
        """Combină sursele: fiecare câmp vine din prima sursă care îl are.
        hints: ce e deja în fișier — {"mood", "grouping", "style", "url"}."""
        out = TagResult()
        hints = hints or {}
        if not artist or not title:
            out.reasons.append("lipsește artistul sau titlul")
            return out
        steps = [("din fișier", lambda: file_genre(hints, self.mapper)),
                 ("Discogs", lambda: self.discogs(artist, title)),
                 ("Bandcamp", lambda: self.bandcamp(hints.get("url", ""), artist)),
                 ("MusicBrainz", lambda: self.musicbrainz(artist, title, duration)),
                 ("Spotify", lambda: self.spotify_lookup(artist, title)),
                 ("Last.fm", lambda: self.lastfm(artist, title)),
                 ("Discogs după artist", lambda: self.discogs_by_artist(artist) if self.by_artist else None)]
        for name, fn in steps:
            missing = [n for n in need if not self._has(out, n)]
            if not missing:
                break
            if r_only_genre_step(name) and "genre" not in missing:
                continue
            r = fn()
            if not r:
                why = skip_reason(name, self, hints)
                out.reasons.append(f"{name}: {why}")
                continue
            if not out.match:
                out.match = f"{r.source}: {r.match}"
            if not out.genre and r.genre:
                out.genre, out.styles, out.raw_genres = r.genre, r.styles, r.raw_genres
                out.sources["genre"] = r.source
            elif not out.styles and r.styles:
                out.styles = r.styles
            if r.only_genre:
                continue
            for f in ("year", "label", "cover_url"):
                if not getattr(out, f) and getattr(r, f):
                    setattr(out, f, getattr(r, f))
                    out.sources[f] = r.source
            if r.cover_url and r.cover_url not in out.cover_urls:
                out.cover_urls.append(r.cover_url)
        out.source = ", ".join(dict.fromkeys(out.sources.values()))
        return out

    @staticmethod
    def _has(r: TagResult, name: str) -> bool:
        return bool({"genre": r.genre, "year": r.year, "label": r.label, "cover": r.cover_url}.get(name))


# --- ajutoare ----------------------------------------------------------------
_SPLIT_ARTIST = re.compile(r"\s+(?:aka|a\.k\.a\.?|feat\.?|ft\.?|featuring|vs\.?|x|&|and|with)\s+|\s*[,/;+]\s*", re.I)
_STOP = {"the", "and", "for", "with", "from", "this", "that", "your", "remix", "mix", "edit", "original",
         "extended", "version", "radio", "club", "dub", "feat", "part", "vocal", "instrumental", "rework"}


def artist_variants(artist: str) -> list[str]:
    """„Marlon Hoffstadt aka DJ Daddy Trance” → [„Marlon Hoffstadt aka DJ Daddy Trance”, „Marlon Hoffstadt”, …]."""
    a = (artist or "").strip()
    out = [a] if a else []
    for p in _SPLIT_ARTIST.split(a):
        p = p.strip()
        if p and p.lower() not in (x.lower() for x in out):
            out.append(p)
    return out[:3] or [a]


def title_variants(title: str) -> list[str]:
    """Titlul întreg → fără remix → fără „feat.” → fără „Pt. 2” / „Part 1” → fără nimic în paranteze."""
    out: list[str] = []

    def add(x):
        x = re.sub(r"\s+", " ", x or "").strip(" -–.,")
        if x and x.lower() not in (o.lower() for o in out):
            out.append(x)

    t = (title or "").strip()
    add(t)
    no_par = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", t)
    no_feat = re.sub(r"\b(feat\.?|ft\.?|featuring)\s.*$", "", no_par, flags=re.I)
    add(no_feat)
    no_part = re.sub(r"[,\s]*\b(pt\.?|part)\s*(\d+|one|two|three|i{1,3}|iv|v)\b", "", no_feat, flags=re.I)
    add(no_part)
    if " - " in no_part:          # „Titlu - Extended Mix”
        add(no_part.split(" - ")[0])
    return out


def rare_word(title: str) -> str:
    """Cel mai lung cuvânt „rar” din titlu (pentru căutarea artist + cuvânt)."""
    tv = title_variants(title)
    words = re.findall(r"[^\W\d_]{4,}", _ascii(tv[-1] if tv else title))
    words = [w for w in words if w not in _STOP]
    return max(words, key=len) if words else ""


def genre_words(text: str) -> list[str]:
    """„techno x trance” → [„Techno”, „Trance”]; „Deep House / Minimal” → [„Deep House”, „Minimal”]."""
    parts = re.split(r"\s+(?:x|and|&|\+|vs\.?)\s+|[,/;|•·]", text or "", flags=re.I)
    return [p.strip().title() for p in parts if p.strip()]


def file_genre(hints: dict, mapper: GenreMapper) -> TagResult | None:
    """Genul ascuns în alte câmpuri ale fișierului (Mood, Grouping, STYLE)."""
    for key, label in (("style", "STYLE"), ("mood", "Mood"), ("grouping", "Grouping")):
        words = genre_words(hints.get(key, ""))
        known = [w for w in words if mapper.family_of(w)]
        if known:
            res = TagResult(styles=known, source=f"din fișier ({label})", only_genre=True,
                            match=hints.get(key, ""))
            res.genre = mapper.classify(known)
            return res
    return None


def parse_bandcamp(html: str, mapper: GenreMapper, artist: str = "") -> TagResult | None:
    tags = [re.sub(r"\s+", " ", t).strip() for t in re.findall(r'class="tag"[^>]*>([^<]+)</a>', html or "")]
    styles = [t.title() for t in tags if mapper.family_of(t)]
    res = TagResult(source="Bandcamp (din URL)")
    res.styles = styles[:5]
    res.genre = mapper.classify(res.styles) if res.styles else ""
    for block in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html or "", re.S):
        try:
            d = json.loads(block)
        except ValueError:
            continue
        date = str(d.get("datePublished") or (d.get("inAlbum") or {}).get("datePublished") or "")
        m = re.search(r"\b(19|20)\d\d\b", date)
        if m:
            res.year = m.group(0)
        pub = d.get("publisher") or {}
        name = pub.get("name", "") if isinstance(pub, dict) else ""
        by = d.get("byArtist") or {}
        by_name = by.get("name", "") if isinstance(by, dict) else ""
        if name and _ascii(name) not in (_ascii(artist), _ascii(by_name)):
            res.label = name        # contul care publică e un label, nu artistul
        img = d.get("image")
        if isinstance(img, list):
            img = img[0] if img else ""
        if isinstance(img, str) and img.startswith("http"):
            res.cover_url = img
        res.match = str(d.get("name") or "")
        break
    return None if res.empty() else res


def r_only_genre_step(name: str) -> bool:
    return name in ("din fișier", "Last.fm", "Discogs după artist")


def skip_reason(name: str, finder: "TagFinder", hints: dict) -> str:
    if name == "Discogs" and not finder.token:
        return "fără token (Setări → Conturi)"
    if name == "Discogs după artist" and not finder.token:
        return "fără token"
    if name == "Last.fm" and not finder.lastfm_key:
        return "fără cheie API (Setări → Conturi)"
    if name == "Bandcamp":
        return "piesa n-are link de Bandcamp în taguri" if "bandcamp" not in (hints.get("url") or "").lower() \
            else "pagina nu are gen / an"
    if name == "Spotify" and not (finder.spotify and getattr(finder.spotify, "logged_in", False)):
        return "nu ești conectat"
    if name == "MusicBrainz" and not finder.use_mb:
        return "oprit din Setări"
    if name == "din fișier":
        return "Mood / Grouping nu arată a gen"
    return "negăsită"
