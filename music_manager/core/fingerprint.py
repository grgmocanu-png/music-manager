"""Dubluri după sunet, cu Chromaprint (fpcalc).

Pentru fiecare piesă se calculează amprenta primelor 2 minute (o listă de numere pe 32 de biți, ~8 pe
secundă). Două fișiere cu amprente aproape identice sunt aceeași înregistrare, chiar dacă au alt nume,
alte taguri, alt bitrate sau alt format. Dacă sunetul e la fel dar durata diferă mult, e probabil altă
versiune (Radio Edit / Extended) — acelea se marchează separat și nu se bifează niciodată singure.

fpcalc vine în installer; dacă lipsește, se descarcă o singură dată din releases-urile Chromaprint.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

import numpy as np

LENGTH_S = 120          # cât din piesă intră în amprentă
SAME = 0.80             # similaritate de la care sunetul e „același” (1 = identic bit cu bit)
VERSION_GAP_S = 8       # sunet la fel, dar durate diferite cu mai mult de atât → altă versiune?
MAX_SHIFT = 80          # cât poate fi decalat începutul (cadre de ~0,124 s → ~10 s)
EXE = "fpcalc.exe" if sys.platform == "win32" else "fpcalc"


# --- fpcalc ---------------------------------------------------------------------------------
def _bases() -> list[str]:
    out = []
    if getattr(sys, "frozen", False):
        out += [getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)]
    out.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return [b for b in out if b]


def fpcalc_path(tools_dir: str = "") -> str | None:
    """fpcalc din aplicație, din folderul de unelte descărcate sau din PATH."""
    places = [os.path.join(b, sub, EXE) for b in _bases() for sub in ("fpcalc", "", "tools")]
    if tools_dir:
        places.append(os.path.join(tools_dir, EXE))
    for p in places:
        if os.path.isfile(p):
            _runnable(p)
            return p
    return shutil.which("fpcalc")


def _runnable(p: str) -> None:
    if sys.platform == "win32":
        return
    try:
        if not os.access(p, os.X_OK):
            os.chmod(p, os.stat(p).st_mode | 0o111)
        if sys.platform == "darwin":
            subprocess.run(["xattr", "-d", "com.apple.quarantine", p],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _platform_word() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def download_fpcalc(tools_dir: str, http_get=None) -> str:
    """Descarcă fpcalc din ultimul release Chromaprint de pe GitHub. Întoarce calea lui."""
    import tarfile
    import zipfile
    if http_get is None:
        def http_get(url):
            import ssl
            import urllib.request
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
            req = urllib.request.Request(url, headers={"User-Agent": "MusicManager"})
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return r.read()
    rel = json.loads(http_get("https://api.github.com/repos/acoustid/chromaprint/releases/latest"))
    word = _platform_word()
    assets = [a for a in rel.get("assets", []) if word in a.get("name", "").lower()
              and "fpcalc" in a.get("name", "").lower()]
    if word == "linux":
        assets = [a for a in assets if "x86_64" in a["name"]] or assets
    if not assets:
        raise RuntimeError("nu am găsit fpcalc pentru acest sistem în releases-urile Chromaprint")
    data = http_get(assets[0]["browser_download_url"])
    os.makedirs(tools_dir, exist_ok=True)
    dest = os.path.join(tools_dir, EXE)
    name = assets[0]["name"].lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next(m for m in z.namelist() if os.path.basename(m) == EXE)
            with z.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            member = next(m for m in t.getmembers() if os.path.basename(m.name) == EXE and m.isfile())
            with t.extractfile(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    _runnable(dest)
    return dest


# --- amprente -------------------------------------------------------------------------------
def _cache_file(cache_dir: str, path: str) -> str:
    try:
        st = os.stat(path)
        sig = f"{os.path.normcase(os.path.abspath(path))}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = path
    return os.path.join(cache_dir, hashlib.sha1(sig.encode("utf-8", "replace")).hexdigest() + ".npy")


def load_cached(cache_dir: str, path: str) -> np.ndarray | None:
    try:
        return np.load(_cache_file(cache_dir, path))
    except (OSError, ValueError):
        return None


def compute(path: str, fpcalc: str, cache_dir: str = "", timeout: float = 120) -> np.ndarray:
    """Amprenta (uint32) a primelor LENGTH_S secunde. Folosește cache-ul dacă fișierul nu s-a schimbat."""
    if cache_dir:
        got = load_cached(cache_dir, path)
        if got is not None:
            return got
    kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    try:
        p = subprocess.run([fpcalc, "-raw", "-json", "-length", str(LENGTH_S), path],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                           timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        raise RuntimeError("fpcalc nu a răspuns")
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError((p.stderr or b"").decode("utf-8", "ignore").strip()[-200:] or "fpcalc a eșuat")
    d = json.loads(p.stdout.decode("utf-8", "ignore"))
    fp = np.array(d.get("fingerprint") or [], dtype=np.int64).astype(np.uint32)
    if cache_dir:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            np.save(_cache_file(cache_dir, path), fp)
        except OSError:
            pass
    return fp


def compute_many(paths, fpcalc: str, cache_dir: str, progress=None, cancelled=None, workers: int = 4):
    """Amprentele mai multor piese, în paralel (fpcalc e un program separat). Întoarce {cale: amprentă}
    și {cale: eroare}."""
    from concurrent.futures import ThreadPoolExecutor
    paths = list(paths)
    progress = progress or (lambda *a: None)
    cancelled = cancelled or (lambda: False)
    out, errors = {}, {}

    def one(p):
        if cancelled():
            return p, None, "oprit"
        try:
            return p, compute(p, fpcalc, cache_dir), None
        except Exception as e:  # noqa: BLE001
            return p, None, str(e)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for i, (p, fp, err) in enumerate(ex.map(one, paths)):
            if fp is not None and len(fp):
                out[p] = fp
            elif err and err != "oprit":
                errors[p] = err
            progress(i + 1, len(paths), p)
    return out, errors


# --- comparare ------------------------------------------------------------------------------
_POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def _bit_errors(x: np.ndarray) -> int:
    return int(_POP[x.view(np.uint8)].sum())


def similarity(a: np.ndarray, b: np.ndarray, max_shift: int = MAX_SHIFT) -> float:
    """Cât de asemănătoare sunt două amprente (0..1), cu decalajul cel mai bun (începuturi tăiate diferit)."""
    if len(a) < 16 or len(b) < 16:
        return 0.0
    best = 0.0
    for off in range(-max_shift, max_shift + 1):
        if off >= 0:
            x, y = a[off:], b
        else:
            x, y = a, b[-off:]
        n = min(len(x), len(y))
        if n < max(16, min(len(a), len(b)) // 2):      # trebuie să se suprapună cel puțin jumătate
            continue
        s = 1.0 - _bit_errors(np.bitwise_xor(x[:n], y[:n])) / (32.0 * n)
        if s > best:
            best = s
    return best


def candidate_pairs(fps: dict, min_shared: int = 6, max_bucket: int = 40) -> set[tuple]:
    """Perechi care merită comparate: au în comun mai multe valori din amprentă (fără să compar tot cu tot).
    Valorile foarte frecvente (liniște, zgomot) se ignoră. Merge cu numpy și pe zeci de mii de piese."""
    keys = list(fps)
    if len(keys) < 2:
        return set()
    vals, ids = [], []
    for i, k in enumerate(keys):
        v = np.unique(fps[k] >> 4)                  # ignorăm ultimii 4 biți (toleranță la re-encodare)
        vals.append(v)
        ids.append(np.full(len(v), i, dtype=np.int32))
    vals = np.concatenate(vals)
    ids = np.concatenate(ids)
    order = np.argsort(vals, kind="stable")
    vals, ids = vals[order], ids[order]
    starts = np.flatnonzero(np.r_[True, vals[1:] != vals[:-1]])
    lens = np.diff(np.r_[starts, len(vals)])
    counts: dict[tuple, int] = {}
    for st, ln in zip(starts[(lens >= 2) & (lens <= max_bucket)], lens[(lens >= 2) & (lens <= max_bucket)]):
        grp = ids[st:st + ln].tolist()
        for x in range(ln):
            gx = grp[x]
            for y in range(x + 1, ln):
                pr = (gx, grp[y]) if gx < grp[y] else (grp[y], gx)
                counts[pr] = counts.get(pr, 0) + 1
    return {(keys[a], keys[b]) for (a, b), c in counts.items() if c >= min_shared}


@dataclass
class SoundMatch:
    a: str
    b: str
    score: float           # 0..1
    version: bool          # durate diferite → aceeași piesă, altă versiune?


def sound_matches(fps: dict, durations: dict, same: float = SAME) -> list[SoundMatch]:
    out = []
    for a, b in candidate_pairs(fps):
        s = similarity(fps[a], fps[b])
        if s >= same:
            da, db = durations.get(a) or 0, durations.get(b) or 0
            out.append(SoundMatch(a, b, s, bool(da and db and abs(da - db) > VERSION_GAP_S)))
    return out


# --- grupuri ----------------------------------------------------------------------------------
@dataclass
class DupInfo:
    reason: dict           # cale → „taguri” / „sunet 97%”
    version: bool = False  # grup cu durate diferite (Radio Edit / Extended)


def merge_groups(tag_groups: list[list], tracks: list, matches: list[SoundMatch]):
    """Unește grupurile după taguri cu perechile găsite după sunet. Întoarce [(grup, DupInfo)].
    Un grup e „altă versiune?” dacă vreo legătură din el e între durate diferite."""
    by_path = {t.path: t for t in tracks}
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    reason: dict[str, str] = {}
    version_roots_pairs = []
    for g in tag_groups:
        for t in g:
            reason.setdefault(t.path, "taguri")
            union(g[0].path, t.path)
    for m in matches:
        if m.a not in by_path or m.b not in by_path:
            continue
        union(m.a, m.b)
        label = f"sunet {round(m.score * 100)}%"
        for p in (m.a, m.b):
            old = reason.get(p)
            if old is None:
                reason[p] = label
            elif old == "taguri":
                reason[p] = f"taguri + {label}"
        if m.version:
            version_roots_pairs.append(m.a)
    groups: dict[str, list] = {}
    for p in parent:
        groups.setdefault(find(p), []).append(by_path[p])
    version_roots = {find(p) for p in version_roots_pairs}
    out = []
    for root, g in groups.items():
        if len(g) < 2:
            continue
        g.sort(key=lambda t: t.quality_score(), reverse=True)
        out.append((g, DupInfo({t.path: reason.get(t.path, "") for t in g}, root in version_roots)))
    from .library import normalize
    out.sort(key=lambda gi: (normalize(gi[0][0].artist), normalize(gi[0][0].title)))
    return out
