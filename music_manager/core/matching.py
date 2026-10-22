"""Potrivirea pieselor din Spotify cu fișierele din bibliotecă.

Ideea: comparăm „miezul” titlului și versiunea (remix, edit, etc.) separat,
plus artistul, iar durata, dacă e cunoscută, trebuie să fie apropiată.
Exemple care trebuie să se potrivească:
  Spotify  "Blue Monday - 1988 Remaster"      ↔  fișier "New Order - Blue Monday.mp3"
  Spotify  "Strobe - Radio Edit"               ↔  "deadmau5 - Strobe (Radio Edit).flac"
  Spotify  "Lose Yourself - Kölsch Remix"      ↔  "... - Lose Yourself (Kolsch Remix).mp3"
și NU:
  "Lose Yourself - Kölsch Remix"  ↔  "Lose Yourself (Original Mix)"
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

from .library import Track
from .spotify import SpotifyTrack

# cuvinte care nu schimbă înregistrarea
_NOISE_VERSION = re.compile(
    r"\b(original( mix| version)?|remaster(ed)?|\d{4}( digital)? remaster(ed)?|remastered \d{4}|"
    r"radio edit|album version|single version|explicit|clean|mono|stereo|"
    r"from .*|feat\.? .*|ft\.? .*|featuring .*|bonus track|deluxe( edition)?)\b", re.I)
_FEAT = re.compile(r"\s*[\(\[]?\s*(feat\.?|ft\.?|featuring|with)\s+[^\)\]\-]*[\)\]]?", re.I)
# linkuri și gunoaie frecvente în numele fișierelor descărcate
_JUNK = re.compile(
    r"(https?://\S+|www\.\S+|\b[\w-]+\.(com|net|org|ru|info|biz)\b|"
    r"[\(\[]\s*(free download|free dl|\d{3} ?kbps|hq|hd|official( music)? video|official audio|"
    r"lyrics?( video)?|audio|video|visuali[sz]er|out now)\s*[\)\]]|"
    r"\bfree download\b|\b\d{3} ?kbps\b|_?\bytmp3\b|\bmp3juices\b)", re.I)


_TRANSLIT = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "ß": "ss", "ł": "l", "Ł": "l",
                           "đ": "d", "Đ": "d", "ı": "i", "œ": "oe", "Œ": "oe", "þ": "th"})


def _ascii(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").translate(_TRANSLIT))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _words(text: str) -> str:
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def split_title(title: str) -> tuple[str, str]:
    """Împarte un titlu în (miez, versiune), normalizate.

    "Strobe - Radio Edit" -> ("strobe", "")         (radio edit e „zgomot”)
    "Song (Kölsch Remix)" -> ("song", "kolsch remix")
    """
    t = _ascii(title)
    t = _JUNK.sub(" ", t)
    t = _FEAT.sub(" ", t)
    versions = re.findall(r"[\(\[]([^\)\]]*)[\)\]]", t)
    core = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", t)
    if " - " in core:
        core, rest = core.split(" - ", 1)
        versions.append(rest)
    ver = " ".join(_NOISE_VERSION.sub(" ", v) for v in versions)
    return _words(core), _words(ver)


def artist_tokens(text: str) -> set[str]:
    t = _ascii(text)
    t = re.sub(r"\b(feat\.?|ft\.?|featuring|vs\.?|and|x|with)\b", ",", t)
    parts = re.split(r"[,&/;+]| x ", t)
    out = set()
    for p in parts:
        w = _words(p)
        w = re.sub(r"^the ", "", w)
        if w:
            out.add(w)
    return out


@dataclass
class LocalKey:
    track: Track
    core: str
    version: str
    artists: set[str]
    blob: str   # artist + nume fișier, pentru căutări de rezervă


def _local_key(t: Track) -> LocalKey:
    title, artist = t.title, t.artist
    stem = os.path.splitext(t.filename)[0]
    stem = _JUNK.sub(" ", stem)
    stem = re.sub(r"^\s*\d{1,3}[\s.\-_]+", "", stem)
    if not title or not artist:
        if " - " in stem:
            a, b = stem.split(" - ", 1)
            artist, title = artist or a, title or b
        else:
            title = title or stem
    core, ver = split_title(title)
    return LocalKey(t, core, ver, artist_tokens(artist), _words(_ascii(f"{artist} {stem}")))


class Matcher:
    def __init__(self, tracks: list[Track]):
        self.by_core: dict[str, list[LocalKey]] = {}
        for t in tracks:
            k = _local_key(t)
            if k.core:
                self.by_core.setdefault(k.core, []).append(k)

    def find(self, st: SpotifyTrack, duration_tol: float = 6.0) -> Track | None:
        core, ver = split_title(st.title)
        if not core:
            return None
        sp_artists = set()
        for a in st.artists:
            sp_artists |= artist_tokens(a)
        best, best_score = None, -1.0
        for k in self.by_core.get(core, []):
            # artist: măcar unul comun, sau numele artistului apare în fișier
            if k.artists and not (k.artists & sp_artists) and not any(a in k.blob for a in sp_artists):
                continue
            if not k.artists and not any(a in k.blob for a in sp_artists):
                continue
            same_ver = ver == k.version or (ver and k.version and (ver in k.version or k.version in ver))
            if not same_ver:
                continue
            score = 1.0
            if st.duration and k.track.duration:
                diff = abs(st.duration - k.track.duration)
                if diff > duration_tol and not (ver == k.version and diff < 20):
                    continue
                score += max(0.0, 1 - diff / duration_tol)
            if ver == k.version:
                score += 1
            if score > best_score:
                best, best_score = k.track, score
        return best


def search_query(st: SpotifyTrack) -> tuple[str, str]:
    """Artist și titlu „curate” pentru căutarea pe Soulseek (fără semne, fără feat.)."""
    artist = st.main_artist
    core, ver = split_title(st.title)
    # păstrăm versiunea (remixul) în căutare, dar fără cuvintele-zgomot
    title = core + (" " + ver if ver else "")
    return _words(_ascii(artist)), title.strip()
