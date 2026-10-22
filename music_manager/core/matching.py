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


def _fmt(sec: float) -> str:
    sec = int(round(sec or 0))
    return f"{sec // 60}:{sec % 60:02}"


class Matcher:
    def __init__(self, tracks: list[Track]):
        self.by_core: dict[str, list[LocalKey]] = {}
        self.by_full: dict[str, list[LocalKey]] = {}     # miez + versiune lipite („titlu kalbata edit”)
        for t in tracks:
            k = _local_key(t)
            if k.core:
                self.by_core.setdefault(k.core, []).append(k)
                self.by_full.setdefault((k.core + " " + k.version).strip(), []).append(k)

    def find_near(self, st: SpotifyTrack, rejected=()) -> tuple[Track, str] | None:
        """Un fișier care seamănă mult, dar nu destul ca să fie sigur — cu motivul.
        Ex.: același titlu, dar fișierul n-are artist; altă versiune; durată diferită."""
        core, ver = split_title(st.title)
        if not core:
            return None
        full = (core + " " + ver).strip()
        sp_artists = set()
        for a in st.artists:
            sp_artists |= artist_tokens(a)
        seen, cands = set(), []
        for k in self.by_core.get(core, []) + self.by_full.get(full, []) + self.by_core.get(full, []):
            if id(k) in seen or k.track.path in rejected:
                continue
            seen.add(id(k))
            cands.append(k)
        best = None
        for k in cands:
            reasons, penalty = [], 0
            artist_ok = bool(k.artists & sp_artists) or any(a in k.blob for a in sp_artists)
            if not artist_ok:
                if k.artists:
                    reasons.append(f"alt artist: {', '.join(sorted(k.artists))}")
                    penalty += 3
                else:
                    reasons.append("fișierul n-are artist")
                    penalty += 1
            kver = k.version
            if (k.core + " " + kver).strip() == full and k.core != core:
                kver = ver                       # versiunea era lipită de titlu
            if not (ver == kver or (ver and kver and (ver in kver or kver in ver))):
                reasons.append(f"altă versiune: {kver or 'fără versiune'} (pe Spotify: {ver or 'fără versiune'})")
                penalty += 2
            if st.duration and k.track.duration and abs(st.duration - k.track.duration) > 6:
                reasons.append(f"durată diferită: {_fmt(k.track.duration)} (pe Spotify {_fmt(st.duration)})")
                penalty += 1
            if not reasons:
                reasons.append("aproape identică")
            if best is None or penalty < best[0]:
                best = (penalty, k.track, "; ".join(reasons))
        if best is None or best[0] >= 5:
            return None
        return best[1], best[2]

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


# cuvinte care nu ajută la căutare (prea comune)
_STOP = set("""a an the of in on at for to from by with and or but is it its i me my you your we our he she
they them this that these those be been are was were do does did not no yes so if as like love baby
oh yeah la le les de des du el los las un una une der die das und mit von feat ft featuring vs x
mix remix edit version original extended radio club dub rework rmx bootleg vip instrumental live
remaster remastered mono stereo pt part vol""".split())


def distinctive_words(text: str, n: int = 1) -> list[str]:
    """Cele mai „rare” cuvinte dintr-un text: lungi și care nu sunt cuvinte comune."""
    words = [w for w in _words(_ascii(text)).split() if w not in _STOP and len(w) >= 3 and not w.isdigit()]
    seen, out = set(), []
    for w in sorted(words, key=lambda w: (-len(w), words.index(w))):
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:n]


def wide_query(st: SpotifyTrack) -> tuple[str, str]:
    """Căutarea „artist + un cuvânt distinctiv din titlu” (+ un cuvânt din numele remixului)."""
    artist = _words(_ascii(st.main_artist))
    core, ver = split_title(st.title)
    words = distinctive_words(core, 1) + distinctive_words(ver, 1)
    if not words:          # titlu foarte scurt / numai cuvinte comune: păstrăm titlul întreg
        return search_query(st)
    return artist, " ".join(words)
