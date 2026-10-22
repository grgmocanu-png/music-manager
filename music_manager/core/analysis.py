"""Analiza audio: BPM, tonalitate, waveform colorat și locurile probabile de drop.

Sunetul e decodat cu ffmpeg (inclus) la mono, 11025 Hz, apoi analizat cu numpy.
Rezultatele se păstrează într-un cache, ca o piesă să fie analizată o singură dată.

Precizie (sincer): BPM-ul iese de obicei corect pe muzică electronică (uneori dublu
sau jumătate); tonalitatea e o estimare — orice program greșește uneori. Valorile
din Traktor / Rekordbox / Serato, când există, au prioritate.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field

import numpy as np

SR = 11025
WAVE_BINS = 1200
ANALYSIS_VERSION = 4

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Profile de tonalitate folosite de KeyFinder (Ibrahim Sha'ath), potrivite pentru muzică electronică
_MAJOR = np.array([7.2390, 3.5035, 3.5845, 2.8451, 5.8190, 4.5587, 2.4478, 6.9947, 3.3911, 4.5561, 4.0739, 4.4593])
_MINOR = np.array([7.0026, 3.1436, 4.3603, 5.4065, 3.6755, 4.0895, 3.9105, 6.2002, 3.6348, 2.8744, 5.3567, 3.8382])


@dataclass
class Analysis:
    bpm: float = 0.0
    bpm_confidence: float = 0.0
    key: str = ""                 # ex. "Am", "F#"
    key_confidence: float = 0.0
    duration: float = 0.0
    # waveform: WAVE_BINS valori 0..255 pentru amplitudine și pentru cele trei benzi
    peak: list = field(default_factory=list)
    low: list = field(default_factory=list)
    mid: list = field(default_factory=list)
    high: list = field(default_factory=list)
    drops: list = field(default_factory=list)   # secunde
    energy: int = 0               # 1..10 (estimare)
    error: str = ""
    analyzed_at: str = ""         # când a fost analizată (ISO)

    @property
    def has_waveform(self) -> bool:
        return bool(self.peak)


# --- decodare --------------------------------------------------------------
def decode(path: str, ffmpeg: str | None = None, sr: int = SR, max_seconds: float = 0) -> np.ndarray:
    if ffmpeg is None:
        from .convert import ffmpeg_path
        ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg lipsește")
    cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", path]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **kwargs)
    if p.returncode != 0 and not p.stdout:
        raise RuntimeError("Nu pot decoda fișierul: " + p.stderr.decode("utf-8", "ignore").strip()[-200:])
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def _stft_mag(x: np.ndarray, n_fft: int, hop: int, chunk_frames: int = 4096):
    """Spectrul de magnitudine, calculat pe bucăți ca să nu consume multă memorie."""
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    n_frames = 1 + (len(x) - n_fft) // hop
    win = np.hanning(n_fft).astype(np.float32)
    out = np.empty((n_frames, n_fft // 2 + 1), dtype=np.float32)
    for start in range(0, n_frames, chunk_frames):
        stop = min(n_frames, start + chunk_frames)
        idx = np.arange(start, stop)[:, None] * hop + np.arange(n_fft)[None, :]
        frames = x[idx] * win
        out[start:stop] = np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)
    return out


# --- waveform ---------------------------------------------------------------
def waveform(x: np.ndarray, sr: int = SR, bins: int = WAVE_BINS):
    """Amplitudinea și energia pe benzi (bas / mediu / înalt), pentru desen."""
    n_fft, hop = 1024, 256
    mag = _stft_mag(x, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    bands = [(30, 160), (160, 2000), (2000, sr / 2)]
    energies = [mag[:, (freqs >= lo) & (freqs < hi)].mean(axis=1) for lo, hi in bands]

    def resample(v, n):
        if len(v) == 0:
            return np.zeros(n)
        edges = np.linspace(0, len(v), n + 1).astype(int)
        return np.array([v[edges[i]:max(edges[i + 1], edges[i] + 1)].max() for i in range(n)])

    def norm(v):
        ref = np.percentile(v, 99.5) if len(v) else 1
        return np.clip(v / (ref + 1e-9), 0, 1)

    # amplitudine din eșantioane
    edges = np.linspace(0, len(x), bins + 1).astype(int)
    peak = np.array([np.abs(x[edges[i]:max(edges[i + 1], edges[i] + 1)]).max() if len(x) else 0
                     for i in range(bins)])
    out = [norm(peak)] + [norm(resample(e, bins)) for e in energies]
    return [[int(i) for i in (v * 255).round()] for v in out], energies[0], hop / sr


# --- drop-uri --------------------------------------------------------------
def find_drops(low_energy: np.ndarray, frame_sec: float, max_drops: int = 3) -> list[float]:
    """Momente în care basul revine puternic după o zonă fără bas (break → drop)."""
    if len(low_energy) == 0:
        return []
    per_sec = max(1, int(round(1 / frame_sec)))
    n = len(low_energy) // per_sec
    if n < 40:
        return []
    sec = low_energy[: n * per_sec].reshape(n, per_sec).mean(axis=1)
    sec = sec / (np.percentile(sec, 95) + 1e-9)
    k = np.ones(2) / 2
    sec = np.convolve(sec, k, mode="same")
    cands = []
    for t in range(20, n - 8):
        before = sec[max(0, t - 8):t].mean()
        after = sec[t:t + 8].mean()
        if before < 0.35 and after > 0.65:
            cands.append((after - before, float(t)))
    cands.sort(reverse=True)
    picked: list[float] = []
    for score, t in cands:
        if all(abs(t - p) > 30 for p in picked):
            # mutăm marcajul pe prima secundă în care basul revine efectiv
            tt = int(t)
            while tt > max(0, t - 8) and sec[tt - 1] > 0.5:
                tt -= 1
            picked.append(float(tt))
        if len(picked) >= max_drops:
            break
    return sorted(picked)


# --- BPM ---------------------------------------------------------------------
def _onset_envelope(x: np.ndarray, sr: int):
    n_fft, hop = 1024, 128
    mag = _stft_mag(x, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    sel = (freqs > 30) & (freqs < 5000)
    logm = np.log1p(10 * mag[:, sel])
    flux = np.maximum(0, np.diff(logm, axis=0)).sum(axis=1)
    # scoatem tendința lentă
    w = int(sr / hop)       # ~1 secundă
    if len(flux) > w:
        trend = np.convolve(flux, np.ones(w) / w, mode="same")
        flux = np.maximum(0, flux - trend)
    return flux.astype(np.float64), sr / hop


def estimate_bpm(x: np.ndarray, sr: int = SR, lo: float = 85, hi: float = 175) -> tuple[float, float]:
    if len(x) < sr * 10:
        return 0.0, 0.0
    # folosim mijlocul piesei (fără intro/outro), maxim ~3 minute
    seg = int(sr * 180)
    if len(x) > seg:
        start = (len(x) - seg) // 2
        x = x[start:start + seg]
    env, fps = _onset_envelope(x, sr)
    env = env - env.mean()
    n = len(env)
    if n < 100:
        return 0.0, 0.0
    f = np.fft.rfft(env, 2 * n)
    acf = np.fft.irfft(f * np.conj(f))[:n]
    acf /= acf[0] + 1e-9

    def score_bpm(b):
        lag = fps * 60 / b
        i = int(lag)
        if i + 1 >= n:
            return 0.0
        frac = lag - i
        v = acf[i] * (1 - frac) + acf[i + 1] * frac
        # armonicele (2, 4 bătăi) întăresc tempo-ul real
        for m, wgt in ((2, 0.5), (4, 0.25)):
            l2 = lag * m
            j = int(l2)
            if j + 1 < n:
                v += wgt * (acf[j] * (1 - (l2 - j)) + acf[j + 1] * (l2 - j))
        return v

    cands = np.arange(lo, hi, 0.5)
    scores = np.array([score_bpm(b) for b in cands])
    # ușoară preferință pentru zona 115–135 (house/techno), fără să excludă restul
    prior = np.exp(-0.5 * (np.log2(cands / 125.0) / 0.9) ** 2)
    best = cands[int(np.argmax(scores * (0.85 + 0.15 * prior)))]
    conf = float(np.clip(scores.max() / (np.abs(scores).mean() * 3 + 1e-9), 0, 1))
    bpm = _refine_bpm(env - env.min(), fps, best)
    # rotunjim aproape de valori întregi (majoritatea pieselor electronice au BPM întreg)
    if abs(bpm - round(bpm)) < 0.15:
        bpm = float(round(bpm))
    return round(bpm, 2), conf


def _refine_bpm(env: np.ndarray, fps: float, approx: float) -> float:
    """Potrivire fină: grila de bătăi care „lovește” cele mai multe atacuri pe toată durata."""
    t_idx = np.arange(len(env))
    best_b, best_s = approx, -1.0
    for b in np.arange(approx - 1.2, approx + 1.2, 0.02):
        period = fps * 60 / b
        n_beats = int((len(env) - 1) / period)
        if n_beats < 8:
            continue
        k = np.arange(n_beats) * period
        phases = np.linspace(0, period, 24, endpoint=False)
        pos = phases[:, None] + k[None, :]
        pos = pos[:, :] [:, :n_beats]
        vals = np.interp(pos, t_idx, env)
        s = vals.sum(axis=1).max() / n_beats
        if s > best_s:
            best_s, best_b = s, b
    return float(best_b)


# --- tonalitate ------------------------------------------------------------
def estimate_key(x: np.ndarray, sr: int = SR) -> tuple[str, float]:
    if len(x) < sr * 10:
        return "", 0.0
    seg = int(sr * 240)
    if len(x) > seg:
        start = (len(x) - seg) // 2
        x = x[start:start + seg]
    n_fft, hop = 8192, 2048
    mag = _stft_mag(x, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    sel = (freqs >= 65) & (freqs <= 2100)
    f = freqs[sel]
    pitch = 12 * np.log2(f / 440.0) + 69
    pc = np.mod(np.round(pitch), 12).astype(int)
    # ponderăm mai puțin binurile aflate între două note
    closeness = 1 - np.minimum(1, 2 * np.abs(pitch - np.round(pitch)))
    spec = np.log1p(mag[:, sel]).mean(axis=0) * closeness
    chroma = np.zeros(12)
    np.add.at(chroma, pc, spec)
    if chroma.sum() <= 0:
        return "", 0.0
    chroma = chroma / chroma.max()

    def corr(a, b):
        a, b = a - a.mean(), b - b.mean()
        return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-9))

    results = []
    for tonic in range(12):
        results.append((corr(chroma, np.roll(_MAJOR, tonic)), NOTES[tonic]))
        results.append((corr(chroma, np.roll(_MINOR, tonic)), NOTES[tonic] + "m"))
    results.sort(reverse=True)
    conf = max(0.0, results[0][0] - results[1][0]) * 5
    return results[0][1], float(min(1.0, conf))


# --- energie ------------------------------------------------------------------
def estimate_energy(x: np.ndarray, sr: int = SR) -> int:
    """Energie 1–10 (estimare, în stilul Mixed In Key): cât de tare, cât bas,
    câtă percuție și cât de „luminos” sună partea principală a piesei."""
    if len(x) < sr * 10:
        return 0
    seg = int(sr * 180)
    if len(x) > seg:
        start = (len(x) - seg) // 2
        x = x[start:start + seg]
    n_fft, hop = 2048, 1024
    mag = _stft_mag(x, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    # volum (RMS pe cadre, partea tare a piesei)
    frame = sr // 10
    nfr = len(x) // frame
    rms = np.sqrt((x[: nfr * frame].reshape(nfr, frame) ** 2).mean(axis=1) + 1e-12)
    loud_db = 20 * np.log10(np.percentile(rms, 90) + 1e-9)            # ~ -25 (încet) … -6 (foarte tare)
    loud = np.clip((loud_db + 24) / 16, 0, 1)
    band = (freqs >= 30) & (freqs < 5000)
    total = mag[:, band].sum(axis=1) + 1e-9
    # bas: ce parte din energie stă în bas (kick + bass)
    low = mag[:, (freqs >= 30) & (freqs < 150)].sum(axis=1)
    bass = float(np.clip(np.median(low / total) / 0.22, 0, 1))
    # percuție: cât de mult se schimbă spectrul de la un cadru la altul (atacuri)
    flux = np.maximum(0, np.diff(mag[:, band], axis=0)).sum(axis=1) / total[1:]
    onset = float(np.clip(np.mean(flux) / 0.35, 0, 1))
    # luminozitate: centrul de greutate al spectrului
    cent = (mag * freqs).sum(axis=1) / (mag.sum(axis=1) + 1e-9)
    bright = np.clip((np.median(cent) - 600) / 1800, 0, 1)
    score = 0.4 * loud + 0.25 * bass + 0.2 * onset + 0.15 * bright
    return int(np.clip(round(1 + score * 9), 1, 10))


# --- tot la un loc, cu cache ----------------------------------------------
def _old_key(path: str) -> str:
    """Cheia veche (depindea de data modificării — se invalida la orice scriere de taguri)."""
    try:
        st = os.stat(path)
        raw = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|{ANALYSIS_VERSION}"
    except OSError:
        raw = path
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()


def cache_key(path: str) -> str:
    """Cheia după cale: analiza rămâne valabilă când se schimbă doar tagurile fișierului."""
    raw = f"{os.path.normcase(os.path.abspath(path))}|v{ANALYSIS_VERSION}"
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()


def alias_key(path: str) -> str:
    """Cheie fără literă de disc / folder: numele fișierului + mărimea.
    Așa analiza se găsește și când discul extern primește altă literă (D: → E:)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return ""
    raw = f"{os.path.basename(path).lower()}|{size}|v{ANALYSIS_VERSION}"
    return "n_" + hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()


def _read(p: str) -> Analysis | None:
    try:
        with open(p, "r", encoding="utf-8") as f:
            return Analysis(**json.load(f))
    except (OSError, ValueError, TypeError):
        return None


def load_cached(path: str, cache_dir: str) -> Analysis | None:
    """Analiza salvată (și cele eșuate — ca să nu fie reîncercate la nesfârșit). None = neanalizată."""
    p = os.path.join(cache_dir, cache_key(path) + ".json")
    a = _read(p)
    if a is not None:
        ak = alias_key(path)
        if ak and not os.path.exists(os.path.join(cache_dir, ak + ".json")):
            _write_alias(path, cache_dir)
        return a
    old = os.path.join(cache_dir, _old_key(path) + ".json")
    a = _read(old)
    if a is not None:                   # migrăm la cheia nouă
        try:
            os.replace(old, p)
        except OSError:
            pass
        _write_alias(path, cache_dir)
        return a
    ak = alias_key(path)
    if ak:
        try:
            with open(os.path.join(cache_dir, ak + ".json"), "r", encoding="utf-8") as f:
                target = json.load(f).get("key", "")
        except (OSError, ValueError, AttributeError):
            target = ""
        a = _read(os.path.join(cache_dir, target + ".json")) if target else None
        if a is not None:               # același fișier, altă cale: copiem la calea nouă
            save_cached(path, cache_dir, a)
            return a
    return None


def cached_date(path: str, cache_dir: str, a: Analysis | None = None) -> str:
    a = a or load_cached(path, cache_dir)
    if a is None:
        return ""
    if a.analyzed_at:
        return a.analyzed_at
    try:
        from datetime import datetime
        mt = os.path.getmtime(os.path.join(cache_dir, cache_key(path) + ".json"))
        return datetime.fromtimestamp(mt).isoformat(timespec="minutes")
    except OSError:
        return ""


def move_cached(old_path: str, new_path: str, cache_dir: str) -> None:
    """Fișierul a fost redenumit / mutat: analiza îl urmează."""
    a = os.path.join(cache_dir, cache_key(old_path) + ".json")
    b = os.path.join(cache_dir, cache_key(new_path) + ".json")
    try:
        if os.path.exists(a) and not os.path.exists(b):
            os.replace(a, b)
    except OSError:
        pass
    _write_alias(new_path, cache_dir)


def _write_alias(path: str, cache_dir: str) -> None:
    ak = alias_key(path)
    if not ak:
        return
    try:
        with open(os.path.join(cache_dir, ak + ".json"), "w", encoding="utf-8") as f:
            json.dump({"key": cache_key(path)}, f)
    except OSError:
        pass


def save_cached(path: str, cache_dir: str, a: Analysis) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    p = os.path.join(cache_dir, cache_key(path) + ".json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(a), f, separators=(",", ":"))
    os.replace(tmp, p)
    _write_alias(path, cache_dir)


def analyze(path: str, cache_dir: str | None = None, ffmpeg: str | None = None,
            bpm_range: tuple[float, float] = (85, 175), force: bool = False) -> Analysis:
    """force=True: analizează din nou chiar dacă există deja (sau a eșuat înainte)."""
    if cache_dir and not force:
        cached = load_cached(path, cache_dir)
        if cached is not None:
            return cached
    from datetime import datetime
    a = Analysis(analyzed_at=datetime.now().isoformat(timespec="minutes"))
    try:
        x = decode(path, ffmpeg)
        a.duration = len(x) / SR
        if len(x) == 0:
            raise RuntimeError("fișier gol")
        (a.peak, a.low, a.mid, a.high), low_e, frame_sec = waveform(x)
        a.drops = find_drops(low_e, frame_sec)
        a.bpm, a.bpm_confidence = estimate_bpm(x, SR, *bpm_range)
        a.key, a.key_confidence = estimate_key(x, SR)
        a.energy = estimate_energy(x, SR)
    except Exception as e:
        a.error = str(e) or e.__class__.__name__
    if cache_dir:
        save_cached(path, cache_dir, a)      # și eșecurile: nu le mai reîncercăm singuri
    return a
