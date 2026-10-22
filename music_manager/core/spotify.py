"""Citirea playlisturilor din Spotify (Web API, autentificare PKCE).

Utilizatorul își creează o singură dată o „aplicație de dezvoltator” pe
developer.spotify.com și lipește Client ID-ul în program. Nu e nevoie de
Client Secret: folosim fluxul PKCE, potrivit pentru aplicații desktop.

Notă (feb. 2026): proprietarul aplicației trebuie să aibă Premium, iar
conținutul unui playlist se poate citi doar dacă e al tău sau colaborezi la el.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
SCOPES = "playlist-read-private playlist-read-collaborative"


class SpotifyError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def parse_playlist_id(link: str) -> str | None:
    """Acceptă link web, link „spotify:playlist:…” sau doar ID-ul."""
    link = (link or "").strip()
    m = re.search(r"playlist[/:]([A-Za-z0-9]{22})", link)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{22}", link):
        return link
    return None


@dataclass
class SpotifyTrack:
    id: str
    title: str
    artists: list[str]
    album: str = ""
    duration: float = 0.0
    year: str = ""
    isrc: str = ""
    added_at: str = ""

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)

    @property
    def main_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.title}" if self.artists else self.title


@dataclass
class SpotifyPlaylist:
    id: str
    name: str
    owner: str = ""
    tracks: list[SpotifyTrack] = field(default_factory=list)
    skipped: int = 0   # episoade, fișiere locale, piese indisponibile


def track_from_json(entry: dict) -> SpotifyTrack | None:
    """Transformă un element din lista playlistului într-un SpotifyTrack.

    Acceptă atât formatul nou (2026, cheia „item”), cât și pe cel vechi („track”).
    """
    if not isinstance(entry, dict):
        return None
    t = entry.get("item") or entry.get("track")
    if not isinstance(t, dict):
        return None
    if t.get("type", "track") != "track" or t.get("is_local"):
        return None
    tid = t.get("id")
    name = (t.get("name") or "").strip()
    if not tid or not name:
        return None
    artists = [a.get("name", "").strip() for a in (t.get("artists") or []) if a.get("name")]
    album = t.get("album") or {}
    rd = album.get("release_date") or ""
    return SpotifyTrack(
        id=tid, title=name, artists=artists, album=(album.get("name") or "").strip(),
        duration=(t.get("duration_ms") or 0) / 1000, year=rd[:4] if rd[:4].isdigit() else "",
        isrc=((t.get("external_ids") or {}).get("isrc") or ""), added_at=entry.get("added_at") or "",
    )


class SpotifyClient:
    """Client minimal; tokenurile sunt salvate de interfață (QSettings) prin callback."""

    def __init__(self, client_id: str, tokens: dict | None = None, on_tokens=None):
        self.client_id = client_id.strip()
        self.tokens = dict(tokens or {})
        self.on_tokens = on_tokens
        self._ctx = _ssl_context()

    # --- autentificare ---
    @property
    def logged_in(self) -> bool:
        return bool(self.tokens.get("refresh_token"))

    def login(self, open_browser, timeout: int = 180) -> None:
        """Deschide pagina de autorizare Spotify și așteaptă răspunsul pe 127.0.0.1."""
        verifier = secrets.token_urlsafe(64)[:100]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        result: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if urllib.parse.urlparse(self.path).path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                if q.get("state", [""])[0] != state:
                    result["error"] = "răspuns invalid (state)"
                elif "error" in q:
                    result["error"] = q["error"][0]
                else:
                    result["code"] = q.get("code", [""])[0]
                ok = "code" in result
                body = ("<h2>Gata! Te poți întoarce în Music Manager.</h2>" if ok else
                        f"<h2>Autorizarea a eșuat: {result.get('error')}</h2>")
                page = f"<html><meta charset='utf-8'><body style='font-family:sans-serif;padding:40px'>{body}</body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page.encode("utf-8"))

            def log_message(self, *args):
                pass

        try:
            server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
        except OSError as e:
            raise SpotifyError(f"Portul {REDIRECT_PORT} e ocupat de alt program ({e}).")
        server.timeout = 1
        params = {
            "client_id": self.client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
            "code_challenge_method": "S256", "code_challenge": challenge, "state": state,
            "scope": SCOPES,
        }
        open_browser(AUTH_URL + "?" + urllib.parse.urlencode(params))
        deadline = time.time() + timeout
        try:
            while not result and time.time() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if "code" not in result:
            raise SpotifyError(result.get("error") or "Nu am primit răspuns de la Spotify (timpul a expirat).")
        self._token_request({
            "grant_type": "authorization_code", "code": result["code"],
            "redirect_uri": REDIRECT_URI, "client_id": self.client_id, "code_verifier": verifier,
        })

    def logout(self):
        self.tokens = {}
        if self.on_tokens:
            self.on_tokens(self.tokens)

    def _token_request(self, data: dict) -> None:
        req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(data).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=30, context=self._ctx) as r:
                tok = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise SpotifyError(f"Spotify a refuzat autentificarea ({e.code}): {detail[:200]}")
        except urllib.error.URLError as e:
            raise SpotifyError(f"Nu mă pot conecta la Spotify: {e.reason}")
        self.tokens["access_token"] = tok["access_token"]
        self.tokens["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        if tok.get("refresh_token"):
            self.tokens["refresh_token"] = tok["refresh_token"]
        if self.on_tokens:
            self.on_tokens(self.tokens)

    def _access_token(self) -> str:
        if not self.logged_in:
            raise SpotifyError("Nu ești conectat la Spotify.")
        if not self.tokens.get("access_token") or time.time() >= self.tokens.get("expires_at", 0):
            self._token_request({"grant_type": "refresh_token",
                                 "refresh_token": self.tokens["refresh_token"],
                                 "client_id": self.client_id})
        return self.tokens["access_token"]

    # --- API ---
    def get(self, url: str, params: dict | None = None, _retry: int = 0) -> dict:
        if not url.startswith("http"):
            url = API + url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self._access_token()})
        try:
            with urllib.request.urlopen(req, timeout=30, context=self._ctx) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and _retry < 5:
                time.sleep(min(int(e.headers.get("Retry-After", "2") or 2), 30))
                return self.get(url, None, _retry + 1)
            if e.code == 401 and _retry < 1:
                self.tokens["access_token"] = ""
                return self.get(url, None, _retry + 1)
            detail = e.read().decode("utf-8", "ignore")
            if e.code == 403:
                raise SpotifyError(
                    "Spotify a refuzat accesul (403). Verifică: ai Premium activ, playlistul e "
                    "al tău (sau colaborezi la el) și contul tău e trecut la „User Management” "
                    "în aplicația de dezvoltator.")
            if e.code == 404:
                raise SpotifyError("Playlistul nu a fost găsit (404). Verifică linkul.")
            raise SpotifyError(f"Eroare Spotify {e.code}: {detail[:200]}")
        except (urllib.error.URLError, socket.timeout) as e:
            raise SpotifyError(f"Nu mă pot conecta la Spotify: {getattr(e, 'reason', e)}")

    def get_playlist(self, playlist_id: str, progress=None) -> SpotifyPlaylist:
        meta = self.get(f"/playlists/{playlist_id}", {"fields": "id,name,owner(display_name)"})
        pl = SpotifyPlaylist(id=playlist_id, name=meta.get("name") or playlist_id,
                             owner=(meta.get("owner") or {}).get("display_name") or "")
        url = f"{API}/playlists/{playlist_id}/items?limit=50"
        seen = 0
        while url:
            page = self.get(url)
            entries = page.get("items") or []
            for e in entries:
                t = track_from_json(e)
                if t:
                    pl.tracks.append(t)
                else:
                    pl.skipped += 1
            seen += len(entries)
            if progress:
                progress(seen, page.get("total") or seen)
            url = page.get("next")
        return pl
