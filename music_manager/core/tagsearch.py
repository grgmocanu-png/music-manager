"""Găsește taguri lipsă (gen, stil, an, label, copertă) online.

Ordinea surselor: Discogs (cel mai bun pentru muzică electronică; cere token
personal gratuit) → MusicBrainz + Cover Art Archive (fără cont) → Spotify.
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

    @property
    def style_text(self) -> str:
        return ", ".join(self.styles[:3])

    def empty(self) -> bool:
        return not (self.genre or self.styles or self.year or self.label or self.cover_url)


class TagFinder:
    def __init__(self, discogs_token: str = "", spotify=None, mapper: GenreMapper | None = None,
                 use_musicbrainz: bool = True, original_release: bool = True):
        self.token = (discogs_token or "").strip()
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
    def discogs(self, artist: str, title: str) -> TagResult | None:
        if not self.token:
            return None
        core, ver = split_title(title)
        params = {"artist": artist, "track": core + (" " + ver if ver else ""), "type": "release",
                  "per_page": 15}
        self._discogs_t.wait()
        data = self._get_json("https://api.discogs.com/database/search?" + urllib.parse.urlencode(params),
                              {"Authorization": f"Discogs token={self.token}"})
        results = (data or {}).get("results") or []
        if not results and ver:
            # fără numele remixului
            params["track"] = core
            self._discogs_t.wait()
            data = self._get_json("https://api.discogs.com/database/search?" + urllib.parse.urlencode(params),
                                  {"Authorization": f"Discogs token={self.token}"})
            results = (data or {}).get("results") or []
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
        res.year = str(years[0]) if (self.original and years) else str(pick.get("year") or "")
        labels = [l for l in (pick.get("label") or []) if l and "not on label" not in l.lower()]
        res.label = re.sub(r"\s*\(\d+\)$", "", labels[0]) if labels else ""
        cover = pick.get("cover_image") or ""
        if cover and "spacer.gif" not in cover:
            res.cover_url = cover
        res.genre = self.mapper.classify(styles, res.raw_genres)
        return res

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
    def find(self, artist: str, title: str, duration: float = 0, need=("genre", "year", "label", "cover")) -> TagResult:
        """Combină sursele: fiecare câmp vine din prima sursă care îl are."""
        out = TagResult()
        if not artist or not title:
            return out
        for fn in (lambda: self.discogs(artist, title),
                   lambda: self.musicbrainz(artist, title, duration),
                   lambda: self.spotify_lookup(artist, title)):
            missing = [n for n in need if not self._has(out, n)]
            if not missing:
                break
            r = fn()
            if not r:
                continue
            if not out.match:
                out.match = f"{r.source}: {r.match}"
            if not out.genre and r.genre:
                out.genre, out.styles, out.raw_genres = r.genre, r.styles, r.raw_genres
                out.sources["genre"] = r.source
            elif not out.styles and r.styles:
                out.styles = r.styles
            for f in ("year", "label", "cover_url"):
                if not getattr(out, f) and getattr(r, f):
                    setattr(out, f, getattr(r, f))
                    out.sources[f] = r.source
            if r.cover_url and r.cover_url not in out.cover_urls:
                out.cover_urls.append(r.cover_url)
        out.source = ", ".join(sorted(set(out.sources.values())))
        return out

    @staticmethod
    def _has(r: TagResult, name: str) -> bool:
        return bool({"genre": r.genre, "year": r.year, "label": r.label, "cover": r.cover_url}.get(name))
