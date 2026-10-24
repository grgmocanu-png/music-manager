"""Citirea playlisturilor din TIDAL (API-ul oficial, openapi.tidal.com/v2).

Fiecare utilizator își face gratuit o aplicație pe developer.tidal.com și pune în Setări Client ID și
Client Secret (rămân doar pe calculatorul lui). Playlisturile publice se citesc doar cu codurile;
cele private cer conectare cu contul TIDAL (PKCE, ca la Spotify).
Piesele întoarse au aceeași formă ca la Spotify (SpotifyTrack), ca restul aplicației să le trateze la fel.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import re
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from .spotify import SpotifyPlaylist, SpotifyTrack, _ssl_context, make_redirect_uri, suggest_free_port

API = "https://openapi.tidal.com/v2"
AUTH_URL = "https://login.tidal.com/authorize"
TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
SCOPES = "playlists.read user.read"
REDIRECT_PORT = 8767
PREFIX = "tidal:"          # ID-urile pieselor TIDAL, ca să nu se amestece cu cele Spotify


class TidalError(Exception):
    pass


def is_tidal(link: str) -> bool:
    return "tidal.com" in (link or "").lower()


def parse_playlist_id(link: str) -> str | None:
    """https://tidal.com/playlist/<uuid>, listen.tidal.com/playlist/…, tidal.com/browse/playlist/… sau doar UUID-ul."""
    link = (link or "").strip()
    m = re.search(r"playlist/([0-9a-fA-F-]{36})", link)
    if m:
        return m.group(1).lower()
    if re.fullmatch(r"[0-9a-fA-F-]{36}", link):
        return link.lower()
    return None


def parse_duration(iso: str) -> float:
    """„PT3M33S” → 213.0"""
    m = re.fullmatch(r"P(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", (iso or "").strip())
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)


class TidalClient:
    def __init__(self, client_id: str, client_secret: str = "", tokens: dict | None = None, on_tokens=None,
                 country: str = "RO", redirect_port: int = REDIRECT_PORT, redirect_path: str = "tidal"):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.tokens = dict(tokens or {})
        self.on_tokens = on_tokens
        self.country = (country or "RO").strip().upper() or "RO"
        self.redirect_port = int(redirect_port or REDIRECT_PORT)
        self.redirect_path = (redirect_path or "tidal").strip().strip("/") or "tidal"
        self.redirect_uri = make_redirect_uri(self.redirect_port, self.redirect_path)
        self._ctx = _ssl_context()
        self._app_token = ("", 0.0)

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def logged_in(self) -> bool:
        """Conectat cu contul TIDAL (pentru playlisturile private)."""
        return bool(self.tokens.get("refresh_token"))

    # --- autentificare ---------------------------------------------------------------
    def _post_token(self, data: dict, basic: bool) -> dict:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if basic and self.client_secret:
            raw = f"{self.client_id}:{self.client_secret}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(data).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30, context=self._ctx) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            raise TidalError(f"TIDAL a refuzat autentificarea ({e.code}). Verifică Client ID și Client Secret "
                             f"în Setări. {detail[:160]}")
        except (urllib.error.URLError, socket.timeout) as e:
            raise TidalError(f"Nu mă pot conecta la TIDAL: {getattr(e, 'reason', e)}")

    def _app_access(self) -> str:
        tok, exp = self._app_token
        if tok and time.time() < exp:
            return tok
        if not self.configured:
            raise TidalError("Pune Client ID și Client Secret TIDAL în Setări → Conturi și API.")
        d = self._post_token({"grant_type": "client_credentials", "client_id": self.client_id}, basic=True)
        self._app_token = (d["access_token"], time.time() + int(d.get("expires_in", 3600)) - 60)
        return self._app_token[0]

    def _user_access(self) -> str:
        if not self.tokens.get("access_token") or time.time() >= self.tokens.get("expires_at", 0):
            d = self._post_token({"grant_type": "refresh_token", "refresh_token": self.tokens["refresh_token"],
                                  "client_id": self.client_id}, basic=bool(self.client_secret))
            self._save(d)
        return self.tokens["access_token"]

    def _save(self, d: dict):
        self.tokens["access_token"] = d["access_token"]
        self.tokens["expires_at"] = time.time() + int(d.get("expires_in", 3600)) - 60
        if d.get("refresh_token"):
            self.tokens["refresh_token"] = d["refresh_token"]
        if self.on_tokens:
            self.on_tokens(self.tokens)

    def login(self, open_browser, timeout: int = 180) -> None:
        """Conectare cu contul TIDAL în browser (pentru playlisturile private)."""
        if not self.client_id:
            raise TidalError("Pune întâi Client ID TIDAL în Setări.")
        verifier = secrets.token_urlsafe(64)[:100]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(16)
        result: dict = {}
        cb_path = "/" + self.redirect_path

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                u = urllib.parse.urlparse(self.path)
                if u.path.rstrip("/") != cb_path:
                    self.send_response(404)
                    self.end_headers()
                    return
                q = urllib.parse.parse_qs(u.query)
                if q.get("state", [""])[0] != state:
                    result["error"] = "răspuns invalid (state)"
                elif "error" in q:
                    result["error"] = q["error"][0]
                else:
                    result["code"] = q.get("code", [""])[0]
                body = ("<h2>Gata! Te poți întoarce în Music Manager.</h2>" if "code" in result else
                        f"<h2>Conectarea a eșuat: {result.get('error')}</h2>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><meta charset='utf-8'><body style='font-family:sans-serif;padding:40px'>"
                                 f"{body}</body></html>".encode("utf-8"))

            def log_message(self, *args):
                pass

        try:
            server = http.server.HTTPServer(("127.0.0.1", self.redirect_port), Handler)
        except OSError:
            alt = suggest_free_port(self.redirect_port + 1)
            raise TidalError(f"Portul {self.redirect_port} e ocupat. În Setări → TIDAL pune alt port"
                             + (f" (de exemplu {alt})" if alt else "") + " și adaugă adresa nouă în aplicația TIDAL.")
        server.timeout = 1
        open_browser(AUTH_URL + "?" + urllib.parse.urlencode({
            "response_type": "code", "client_id": self.client_id, "redirect_uri": self.redirect_uri,
            "scope": SCOPES, "code_challenge_method": "S256", "code_challenge": challenge, "state": state}))
        deadline = time.time() + timeout
        try:
            while not result and time.time() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if "code" not in result:
            raise TidalError(result.get("error") or "Nu am primit răspuns de la TIDAL (timpul a expirat).")
        self._save(self._post_token({"grant_type": "authorization_code", "code": result["code"],
                                     "redirect_uri": self.redirect_uri, "client_id": self.client_id,
                                     "code_verifier": verifier}, basic=bool(self.client_secret)))

    def logout(self):
        self.tokens = {}
        if self.on_tokens:
            self.on_tokens(self.tokens)

    # --- API --------------------------------------------------------------------------------
    def get(self, path: str, params: dict | None = None, user: bool | None = None, _retry: int = 0) -> dict:
        url = path if path.startswith("http") else API + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, safe="[],")
        use_user = self.logged_in if user is None else user
        token = self._user_access() if use_user else self._app_access()
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token,
                                                   "Accept": "application/vnd.api+json"})
        try:
            with urllib.request.urlopen(req, timeout=30, context=self._ctx) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 429 and _retry < 5:
                time.sleep(min(int(e.headers.get("Retry-After", "2") or 2), 30))
                return self.get(url, None, user, _retry + 1)
            if e.code in (401, 403, 404) and use_user is False and self.logged_in and _retry < 1:
                return self.get(url, None, True, _retry + 1)      # poate e privat: încercăm cu contul tău
            if e.code == 404:
                raise TidalError("Playlistul TIDAL nu a fost găsit (404). E privat? Conectează-te la TIDAL din "
                                 "Setări, apoi verifică din nou.")
            if e.code in (401, 403):
                raise TidalError("TIDAL a refuzat accesul. Dacă playlistul e privat, conectează-te la TIDAL din Setări.")
            raise TidalError(f"Eroare TIDAL {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
        except (urllib.error.URLError, socket.timeout) as e:
            raise TidalError(f"Nu mă pot conecta la TIDAL: {getattr(e, 'reason', e)}")

    def _artists_for(self, track_ids: list[str]) -> dict[str, list[str]]:
        """{id piesă: [artiști]} — cerute în loturi de câte 20."""
        out: dict[str, list[str]] = {}
        for i in range(0, len(track_ids), 20):
            chunk = track_ids[i:i + 20]
            d = self.get("/tracks", {"countryCode": self.country, "filter[id]": ",".join(chunk), "include": "artists"})
            names = {a.get("id"): (a.get("attributes") or {}).get("name", "")
                     for a in d.get("included") or [] if a.get("type") == "artists"}
            for t in d.get("data") or []:
                rel = (((t.get("relationships") or {}).get("artists") or {}).get("data")) or []
                out[t.get("id")] = [names.get(r.get("id"), "") for r in rel if names.get(r.get("id"))]
        return out

    def get_playlist(self, playlist_id: str, progress=None) -> SpotifyPlaylist:
        meta = self.get(f"/playlists/{playlist_id}", {"countryCode": self.country})
        attrs = ((meta.get("data") or {}).get("attributes")) or {}
        pl = SpotifyPlaylist(id=playlist_id, name=attrs.get("name") or playlist_id)
        url = f"/playlists/{playlist_id}/relationships/items"
        params: dict | None = {"countryCode": self.country, "include": "items"}
        order, info, added = [], {}, {}
        pages = 0
        while url and pages < 200:
            page = self.get(url, params)
            params = None
            for ref in page.get("data") or []:
                if ref.get("type") != "tracks":
                    pl.skipped += 1
                    continue
                order.append(ref.get("id"))
                added[ref.get("id")] = ((ref.get("meta") or {}).get("addedAt")) or ""
            for inc in page.get("included") or []:
                if inc.get("type") == "tracks":
                    info[inc.get("id")] = inc.get("attributes") or {}
            if progress:
                progress(len(order), len(order))
            nxt = ((page.get("links") or {}).get("next")) or ""
            url = nxt if nxt else ""
            pages += 1
        artists = self._artists_for([t for t in order if t])
        for tid in order:
            a = info.get(tid) or {}
            title = (a.get("title") or "").strip()
            if a.get("version"):
                title = f"{title} ({a['version']})"
            if not tid or not title:
                pl.skipped += 1
                continue
            pl.tracks.append(SpotifyTrack(
                id=PREFIX + tid, title=title, artists=artists.get(tid) or [], duration=parse_duration(a.get("duration")),
                isrc=a.get("isrc") or "", added_at=added.get(tid, ""), year=""))
        return pl
