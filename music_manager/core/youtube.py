"""Citirea playlisturilor YouTube — doar lista de piese (titlu, canal, durată).

Sunetul NU se ia de pe YouTube: lista e folosită doar ca să știm ce să căutăm
pe Soulseek, exact ca la Spotify. Lista se citește cu yt-dlp, fără cont și
fără cheie API.
"""
from __future__ import annotations

import re

from .spotify import SpotifyPlaylist, SpotifyTrack

_SKIP_TITLES = {"[deleted video]", "[private video]", "[unavailable video]"}
_JUNK = re.compile(
    r"\s*[\(\[\{]\s*(official( music| lyric| hd)?( video| audio| visuali[sz]er)?|music video|lyrics?( video)?|"
    r"audio|video|hd|hq|4k|visuali[sz]er|free (download|dl)|free|out now|premiere|clip officiel|"
    r"\d{3} ?kbps)\s*[\)\]\}]", re.I)
_JUNK_FREE = re.compile(r"\s*(\|\s*)?\b(free download|free dl|out now|\[?free\]?)\b\s*$", re.I)
_SEPARATORS = (" - ", " – ", " — ", " -- ", " ~ ", " | ")


def parse_playlist_id(link: str) -> str | None:
    link = (link or "").strip()
    m = re.search(r"[?&]list=([A-Za-z0-9_-]{10,})", link)
    if m and ("youtube.com" in link or "youtu.be" in link or "music.youtube" in link):
        return m.group(1)
    return None


def is_youtube(link: str) -> bool:
    return parse_playlist_id(link) is not None


def clean_channel(name: str) -> str:
    name = re.sub(r"\s*-\s*Topic$", "", name or "").strip()   # canalele auto „Artist - Topic”
    name = re.sub(r"\s*(VEVO|Official|Music)$", "", name, flags=re.I).strip()
    return name


def parse_title(title: str, channel: str = "") -> tuple[str, str]:
    """„ARTIST - Track (Bootleg) [Free DL]” -> („ARTIST”, „Track (Bootleg)”).

    Dacă titlul nu conține artistul, folosim numele canalului.
    """
    t = title or ""
    for _ in range(3):
        t = _JUNK.sub("", t)
    t = _JUNK_FREE.sub("", t).strip(" -|")
    t = re.sub(r"\s+", " ", t).strip()
    for sep in _SEPARATORS:
        if sep in t:
            artist, rest = t.split(sep, 1)
            artist, rest = artist.strip(" \"'"), rest.strip(" \"'")
            if artist and rest:
                return artist, rest
    # „Artist "Titlu"” sau doar titlu -> artistul e canalul
    m = re.match(r'^(.+?)\s+["“](.+?)["”]', t)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return clean_channel(channel), t


def fetch_playlist(url_or_id: str, progress=None) -> SpotifyPlaylist:
    """Citește playlistul. Întoarce același tip ca la Spotify, ca restul programului să fie comun."""
    import yt_dlp

    pid = parse_playlist_id(url_or_id) or url_or_id
    url = f"https://www.youtube.com/playlist?list={pid}"
    opts = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True,
            "skip_download": True, "ignoreerrors": True, "noprogress": True}
    if progress:
        progress(0, 0)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as e:
        raise RuntimeError(f"Nu am putut citi playlistul YouTube: {e}") from e
    if not info:
        raise RuntimeError("Nu am putut citi playlistul YouTube (e privat sau linkul e greșit?).")
    pl = SpotifyPlaylist(id=pid, name=info.get("title") or pid,
                         owner=info.get("channel") or info.get("uploader") or "")
    entries = [e for e in (info.get("entries") or [])]
    for i, e in enumerate(entries):
        if not e or not e.get("id"):
            pl.skipped += 1
            continue
        title = (e.get("title") or "").strip()
        if not title or title.lower() in _SKIP_TITLES:
            pl.skipped += 1
            continue
        artist, name = parse_title(title, e.get("channel") or e.get("uploader") or "")
        pl.tracks.append(SpotifyTrack(id=e["id"], title=name, artists=[artist] if artist else [],
                                      duration=float(e.get("duration") or 0)))
        if progress and i % 50 == 0:
            progress(i, len(entries))
    return pl
