"""Descărcare de pe Soulseek prin Sockseek (fostul sldl / slsk-batchdl).

Sockseek e un program separat (open-source, AGPL-3.0) inclus lângă aplicație.
Îi dăm un CSV cu piesele lipsă (artist, titlu, album, durată) și citim
evenimentele JSON pe care le scrie, ca să arătăm progresul.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import queue as queue_mod
import time
from dataclasses import dataclass, field
from typing import Callable

from . import tags as tagmod
from .library import safe_name, unique_path
from .matching import search_query
from .spotify import SpotifyTrack

QUALITY = {
    "mp3_320": "MP3 320 (acceptă și ~256 / V0 dacă nu există 320)",
    "mp3_320_strict": "Doar MP3 320",
    "flac_first": "FLAC dacă există, altfel MP3 320",
}


def sockseek_path(custom: str = "") -> str | None:
    """Caută executabilul: setarea utilizatorului, lângă aplicație, apoi în PATH."""
    if custom and os.path.isfile(custom):
        return custom
    exe = "sockseek.exe" if sys.platform == "win32" else "sockseek"
    bases = []
    if getattr(sys, "frozen", False):
        bases += [getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)]
    bases.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    for b in bases:
        for sub in ("sockseek", "", "tools"):
            p = os.path.join(b, sub, exe)
            if b and os.path.isfile(p):
                return p
    return shutil.which(exe) or shutil.which("sldl")


@dataclass
class DownloadItem:
    track: SpotifyTrack
    artist_query: str = ""
    title_query: str = ""

    def __post_init__(self):
        if not (self.artist_query or self.title_query):
            self.artist_query, self.title_query = search_query(self.track)


@dataclass
class DownloadEvent:
    kind: str                 # searching | downloading | progress | done | failed | log
    index: int = -1           # poziția în lista trimisă (-1 dacă nu se știe)
    text: str = ""
    percent: float = 0.0
    path: str = ""
    code: str = ""            # codul motivului de eșec (ex. NoSuitableFileFound)


@dataclass
class DownloadSummary:
    done: list = field(default_factory=list)       # (index, cale finală)
    failed: list = field(default_factory=list)     # (index, motiv)
    cancelled: bool = False
    stopped_now: list = field(default_factory=list)   # indecși opriți în timpul descărcării
    exit_code: int = 0
    log_tail: list = field(default_factory=list)


FAILURE_TEXT = {
    "NoSuitableFileFound": "există, dar nu în calitatea sau durata cerută",
    "NoResults": "negăsită pe Soulseek",
    "InvalidSearchString": "căutare invalidă",
    "AllDownloadsFailed": "toate încercările de descărcare au eșuat",
    "Cancelled": "anulată",
    "Other": "eroare",
}


def write_config(path: str, username: str, password: str) -> None:
    """Configurația cu contul Soulseek, ca parola să nu apară în linia de comandă."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"username = {username}\npassword = {password}\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def write_csv(path: str, items: list[DownloadItem]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["Artist", "Title", "Album", "Length"])
        for it in items:
            w.writerow([it.artist_query, it.title_query, "", int(round(it.track.duration)) or ""])


def build_command(exe: str, csv_path: str, out_dir: str, config_path: str,
                  quality: str = "mp3_320", length_tol: int = 5, wide: bool = False) -> list[str]:
    """wide=True: căutare mai largă pentru piesele negăsite (mai lentă, mai tolerantă)."""
    if wide:
        length_tol = max(length_tol, 15)
    cmd = [exe, csv_path, "--input-type", "csv", "-c", config_path,
           "-o", out_dir,
           "--name-format", "{sartist( - )stitle|filename}",  # redenumim noi apoi, din Spotify
           "--length-tol", str(length_tol), "--pref-length-tol", "2",
           "--no-skip-existing", "--no-write-index",
           "--fast-search", "--progress-json", "--no-progress"]
    if quality == "mp3_320_strict":
        cmd += ["--format", "mp3", "--min-bitrate", "315", "--pref-format", "mp3"]
    elif quality == "flac_first":
        cmd += ["--format", "flac,mp3", "--pref-format", "flac", "--min-bitrate", "240"]
    else:
        cmd += ["--format", "mp3", "--min-bitrate", "240", "--pref-format", "mp3",
                "--pref-min-bitrate", "315"]
    if wide:
        cmd += ["--desperate", "--remove-ft", "--remove-brackets"]
    return cmd


class EventParser:
    """Transformă liniile JSON de la Sockseek în evenimente pentru interfață."""

    def __init__(self, items: list[DownloadItem]):
        self.items = items
        self._by_key: dict[tuple, list[int]] = {}
        for i, it in enumerate(items):
            self._by_key.setdefault((it.artist_query.lower(), it.title_query.lower()), []).append(i)
        self._job_index: dict[str, int] = {}
        self._last_download_index = -1

    def _index(self, data: dict) -> int:
        key = ((data.get("artist") or "").lower(), (data.get("title") or "").lower())
        lst = self._by_key.get(key)
        return lst[0] if lst else -1

    def parse(self, line: str) -> DownloadEvent | None:
        line = line.strip()
        if not line.startswith("{"):
            return DownloadEvent("log", text=line) if line else None
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return DownloadEvent("log", text=line)
        typ, data = ev.get("type"), ev.get("data") or {}
        if typ == "search_start":
            return DownloadEvent("searching", self._index(data))
        if typ == "download_start":
            idx = self._index(data)
            self._last_download_index = idx
            name = os.path.basename((data.get("filename") or "").replace("\\", "/"))
            return DownloadEvent("downloading", idx, f"{name} (de la {data.get('username')})")
        if typ == "download_progress":
            idx = self._index(data) if data.get("title") else self._last_download_index
            return DownloadEvent("progress", idx, percent=float(data.get("percent") or 0))
        if typ == "track_state":
            idx = self._index(data)
            outcome = data.get("terminalOutcome") or ""
            if outcome == "Succeeded" and data.get("downloadPath"):
                br = data.get("bitRate")
                ext = (data.get("extension") or "").upper()
                return DownloadEvent("done", idx, f"{ext} {br} kbps" if br else ext,
                                     path=data["downloadPath"])
            if outcome in ("Failed", "Skipped", "Cancelled") or data.get("failureReason"):
                reason = data.get("failureReason") or data.get("skipReason") or outcome
                # dacă (Skipped/AlreadyExists) avem totuși calea, tratăm ca reușit
                if data.get("downloadPath") and reason == "AlreadyExists":
                    return DownloadEvent("done", idx, "exista deja", path=data["downloadPath"])
                return DownloadEvent("failed", idx, FAILURE_TEXT.get(reason, str(reason)), code=str(reason))
        if typ == "extraction_failed":
            return DownloadEvent("log", text="Eroare la citirea listei: " + str(data.get("reason")))
        return None


def pretty_title(title: str) -> str:
    """„Strobe - Radio Edit” -> „Strobe (Radio Edit)” (convenția obișnuită în DJ)."""
    if " - " in title:
        a, b = title.split(" - ", 1)
        return f"{a.strip()} ({b.strip()})"
    return title.strip()


def pretty_artist(track: SpotifyTrack) -> str:
    """Artiștii, fără cei care apar deja în titlu (de ex. remixerul)."""
    tl = track.title.lower()
    names = [a for i, a in enumerate(track.artists) if i == 0 or a.lower() not in tl]
    return ", ".join(names)


def finalize_file(path: str, track: SpotifyTrack, out_dir: str) -> str:
    """Pune numele și tagurile curate din Spotify pe fișierul descărcat."""
    artist, title = pretty_artist(track), pretty_title(track.title)
    ext = os.path.splitext(path)[1].lower()
    name = safe_name(f"{artist} - {title}") or os.path.splitext(os.path.basename(path))[0]
    dest = os.path.join(out_dir, name + ext)
    if os.path.normcase(os.path.abspath(dest)) != os.path.normcase(os.path.abspath(path)):
        dest = unique_path(dest)
        shutil.move(path, dest)
    try:
        current = tagmod.read_tags(dest).fields
        new = {"artist": artist, "title": title, "album": track.album, "year": track.year}
        # păstrăm ce exista deja (gen, BPM, tonalitate) și suprascriem doar identitatea piesei
        tagmod.write_tags(dest, {**current, **{k: v for k, v in new.items() if v}})
    except Exception:
        pass
    return dest


def _flatten_into(path: str, out_dir: str) -> str:
    """Mută fișierul descărcat direct în out_dir (Sockseek poate crea un subfolder)."""
    if not path or not os.path.exists(path):
        return path
    if os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(os.path.abspath(out_dir)):
        return path
    dest = unique_path(os.path.join(out_dir, os.path.basename(path)))
    shutil.move(path, dest)
    d = os.path.dirname(path)
    try:
        while os.path.normcase(d) != os.path.normcase(os.path.abspath(out_dir)) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)
    except OSError:
        pass
    return dest


def cleanup_incomplete(out_dir: str) -> int:
    """Șterge fișierele descărcate pe jumătate (Sockseek le pune extensia .incomplete)."""
    n = 0
    for root, dirs, files in os.walk(out_dir, topdown=False):
        for f in files:
            if f.lower().endswith(".incomplete"):
                try:
                    os.remove(os.path.join(root, f))
                    n += 1
                except OSError:
                    pass
        if os.path.normcase(os.path.abspath(root)) != os.path.normcase(os.path.abspath(out_dir)):
            try:
                if not os.listdir(root):
                    os.rmdir(root)
            except OSError:
                pass
    return n


STOP_AFTER_CURRENT = "after_current"
STOP_NOW = "now"


def run_download(items: list[DownloadItem], out_dir: str, username: str, password: str,
                 quality: str = "mp3_320", exe: str | None = None, work_dir: str | None = None,
                 on_event: Callable[[DownloadEvent], None] | None = None,
                 cancelled: Callable[[], object] | None = None, wide: bool = False) -> DownloadSummary:
    """Rulează Sockseek și raportează evenimentele.

    cancelled() poate întoarce:
      False/None          — continuă
      STOP_AFTER_CURRENT  — termină piesele care se descarcă deja, apoi oprește
      STOP_NOW / True     — oprește imediat (fișierele începute se șterg)
    În ambele cazuri nu rămân fișiere pe jumătate în folder.
    """
    exe = exe or sockseek_path()
    if not exe:
        raise FileNotFoundError("Nu găsesc Sockseek (programul care descarcă de pe Soulseek).")
    if not username or not password:
        raise ValueError("Completează utilizatorul și parola de Soulseek în Setări.")
    os.makedirs(out_dir, exist_ok=True)
    work_dir = work_dir or tempfile.mkdtemp(prefix="mm_slsk_")
    os.makedirs(work_dir, exist_ok=True)
    csv_path = os.path.join(work_dir, "lista.csv")
    conf_path = os.path.join(work_dir, "sockseek.conf")
    write_csv(csv_path, items)
    write_config(conf_path, username, password)

    cmd = build_command(exe, csv_path, out_dir, conf_path, quality, wide=wide)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, cwd=work_dir, **kwargs)

    lines: queue_mod.Queue = queue_mod.Queue()

    def reader():
        for raw in proc.stdout:
            lines.put(raw)
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()

    parser = EventParser(items)
    summary = DownloadSummary()
    active: set[int] = set()        # piese care se descarcă acum
    waiting_for: set[int] | None = None   # la „oprește după piesele în lucru”
    stop_mode = None

    def kill():
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    try:
        while True:
            # cererea de oprire
            if cancelled and stop_mode is None:
                req = cancelled()
                if req:
                    stop_mode = STOP_AFTER_CURRENT if req == STOP_AFTER_CURRENT else STOP_NOW
                    summary.cancelled = True
                    if stop_mode == STOP_NOW:
                        summary.stopped_now = sorted(active)
                        kill()
                        break
                    waiting_for = set(active)
            elif cancelled and stop_mode == STOP_AFTER_CURRENT and cancelled() == STOP_NOW:
                stop_mode = STOP_NOW
                summary.stopped_now = sorted(active)
                kill()
                break
            if stop_mode == STOP_AFTER_CURRENT and waiting_for is not None and not (waiting_for & active):
                summary.stopped_now = sorted(active)   # descărcări pornite după cerere
                kill()
                break

            try:
                raw = lines.get(timeout=0.5)
            except queue_mod.Empty:
                continue
            if raw is None:
                break
            ev = parser.parse(raw.decode("utf-8", "ignore"))
            if ev is None:
                continue
            if ev.kind == "log":
                summary.log_tail = (summary.log_tail + [ev.text])[-40:]
            elif ev.kind == "downloading":
                if stop_mode == STOP_AFTER_CURRENT:
                    continue   # nu mai raportăm piese noi; vor fi oprite
                active.add(ev.index)
            elif ev.kind == "done":
                active.discard(ev.index)
                try:
                    ev.path = _flatten_into(ev.path, out_dir)
                    if 0 <= ev.index < len(items) and os.path.exists(ev.path):
                        ev.path = finalize_file(ev.path, items[ev.index].track, out_dir)
                except OSError:
                    pass
                summary.done.append((ev.index, ev.path))
            elif ev.kind == "failed":
                active.discard(ev.index)
                summary.failed.append((ev.index, ev.text, ev.code))
            if on_event:
                on_event(ev)
        if proc.poll() is None:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                kill()
    finally:
        if proc.poll() is None:
            kill()
        try:
            os.remove(conf_path)    # nu lăsăm parola pe disc
        except OSError:
            pass
        time.sleep(0.3)   # Windows eliberează fișierele cu o mică întârziere
        cleanup_incomplete(out_dir)
    summary.exit_code = proc.returncode or 0
    if (summary.exit_code and not summary.cancelled and not summary.done and not summary.failed):
        tail = "\n".join(summary.log_tail[-8:])
        if "login" in tail.lower() or "password" in tail.lower():
            raise RuntimeError("Soulseek a refuzat autentificarea. Verifică utilizatorul și parola.\n\n" + tail)
        raise RuntimeError(f"Sockseek s-a oprit cu eroarea {summary.exit_code}.\n\n{tail}")
    return summary
