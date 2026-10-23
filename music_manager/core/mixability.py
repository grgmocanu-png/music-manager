"""Mixabilitate: cât de ușor se mixează o piesă (și cum), fără AI — doar analiză de semnal.

Pentru Explore: BPM constant, 4/4, cât din piesă are puls clar, intro/outro ritmic,
cel mai bun segment de mixat, dacă după drop urmează puls, cât de prezentă e vocea,
dacă are tobe, și de aici un tip (acapella / vocal / instrumental / complet),
o folosire sugerată și un scor 1–10 cu motivele lui.

Precizie (sincer): e o estimare. Merge bine pe muzică cu tobe clare; la jazz, world sau
piese fără tobe spune des „probabil”. Un solo de sax poate părea „voce”.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass

import numpy as np

from . import analysis as an

MIX_VERSION = 1
WIN, HOP = 8.0, 4.0          # ferestre de analiză (secunde)

ACAPELLA_WORDS = re.compile(r"\b(a ?cap+el+a|acapella|acappella|vocals? only|vocal|vox|spoken|"
                            r"spoken word|interlude|intro)\b", re.I)


@dataclass
class MixInfo:
    score: int = 0                 # 1..10
    reasons: str = ""
    bpm: float = 0.0
    bpm_const: bool = False
    bpm_drift: float = 0.0         # ± BPM
    four: str = ""                 # da / probabil / nu
    pulse: int = 0                 # % din piesă cu beat clar
    drop_pulse: str = ""           # da / nu / —
    intro_s: int = 0
    outro_s: int = 0
    seg_start: float = 0.0
    seg_end: float = 0.0
    voice: int = 0                 # 0..100
    drums: str = ""                # da / puțin / nu
    kind: str = ""                 # acapella / vocal + puțin instrumental / instrumental / complet
    use: str = ""                  # direct / segment / overlay / intro-outro set / ½ tempo
    key: str = ""
    energy: int = 0
    duration: float = 0.0
    error: str = ""
    analyzed_at: str = ""
    version: int = MIX_VERSION

    @property
    def segment(self) -> str:
        if self.seg_end <= self.seg_start:
            return ""
        f = lambda t: f"{int(t) // 60}:{int(t) % 60:02}"  # noqa: E731
        return f"{f(self.seg_start)}–{f(self.seg_end)}"


# --- cache -------------------------------------------------------------------
def _key(path: str) -> str:
    raw = f"{os.path.normcase(os.path.abspath(path))}|mix{MIX_VERSION}"
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()


def load_cached(path: str, cache_dir: str) -> MixInfo | None:
    try:
        with open(os.path.join(cache_dir, _key(path) + ".json"), "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("version") != MIX_VERSION:
            return None
        return MixInfo(**d)
    except (OSError, ValueError, TypeError):
        return None


def save_cached(path: str, cache_dir: str, m: MixInfo) -> None:
    os.makedirs(cache_dir, exist_ok=True)
    p = os.path.join(cache_dir, _key(path) + ".json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(asdict(m), f)
    os.replace(p + ".tmp", p)


def move_cached(old: str, new: str, cache_dir: str) -> None:
    a, b = (os.path.join(cache_dir, _key(x) + ".json") for x in (old, new))
    try:
        if os.path.exists(a) and not os.path.exists(b):
            os.replace(a, b)
    except OSError:
        pass


# --- analiza -----------------------------------------------------------------
def _features(x: np.ndarray, sr: int):
    hop = 256 if len(x) < sr * 480 else 512
    mag = an._stft_mag(x, 1024, hop)
    freqs = np.fft.rfftfreq(1024, 1 / sr)
    fps = sr / hop
    logm = np.log1p(10 * mag)
    flux = np.maximum(0, np.diff(logm, axis=0))
    onset = flux[:, (freqs > 30) & (freqs < 5000)].sum(axis=1)
    kick = flux[:, (freqs > 30) & (freqs < 160)].sum(axis=1)
    total = mag[:, (freqs > 30) & (freqs < 5000)].sum(axis=1) + 1e-9
    low = mag[:, (freqs > 30) & (freqs < 150)].sum(axis=1)
    vocal = mag[:, (freqs > 300) & (freqs < 3400)].sum(axis=1)
    return onset, kick, total, low, vocal, fps


def _local_tempo(env: np.ndarray, fps: float, lo=70.0, hi=180.0):
    """(bpm, tărie 0..1) într-o fereastră: autocorelația atacurilor."""
    e = env - env.mean()
    n = len(e)
    if n < fps * 3 or not np.any(e):
        return 0.0, 0.0
    f = np.fft.rfft(e, 2 * n)
    acf = np.fft.irfft(f * np.conj(f))[:n]
    acf /= acf[0] + 1e-9
    lags = np.arange(int(fps * 60 / hi), min(n - 1, int(fps * 60 / lo)) + 1)
    if len(lags) < 3:
        return 0.0, 0.0
    vals = acf[lags]
    i = int(np.argmax(vals))
    return float(60 * fps / lags[i]), float(max(0.0, vals[i]))


def _fold(b: float, ref: float) -> float:
    """Aduce un tempo local în aceeași octavă cu tempo-ul piesei (×2 / ÷2)."""
    if not b or not ref:
        return b
    while b < ref * 0.75:
        b *= 2
    while b > ref * 1.5:
        b /= 2
    return b


def analyze_samples(x: np.ndarray, sr: int = an.SR, bpm: float = 0.0, key: str = "", energy: int = 0,
                    drops=(), name: str = "") -> MixInfo:
    from datetime import datetime
    m = MixInfo(analyzed_at=datetime.now().isoformat(timespec="minutes"), key=key, energy=energy)
    m.duration = len(x) / sr
    if m.duration < 20:
        m.error = "piesă prea scurtă"
        return m
    onset, kick, total, low, vocal, fps = _features(x, sr)
    if not bpm:
        bpm, _c = an.estimate_bpm(x, sr, 70, 180)
    m.bpm = bpm
    win, hop = int(WIN * fps), int(HOP * fps)
    starts = list(range(0, max(1, len(onset) - win), hop)) or [0]
    strengths, tempos, kicks = [], [], []
    for s0 in starts:
        b, st = _local_tempo(onset[s0:s0 + win], fps)
        kb, kst = _local_tempo(kick[s0:s0 + win], fps)
        strengths.append(st)
        tempos.append(_fold(b, bpm))
        kicks.append(kst)
    strengths, tempos, kicks = np.array(strengths), np.array(tempos), np.array(kicks)
    pulse_w = strengths > 0.30
    if bpm:
        pulse_w &= np.abs(tempos - bpm) < max(3.0, bpm * 0.04)
    m.pulse = int(round(100 * pulse_w.mean()))
    if m.pulse < 10:
        m.bpm = 0.0          # fără puls, „BPM-ul” n-are sens
    # BPM constant: cât variază tempo-ul local pe porțiunile cu puls
    if pulse_w.sum() >= 3:
        t = tempos[pulse_w]
        m.bpm_drift = round(float(np.percentile(t, 90) - np.percentile(t, 10)) / 2, 1)
        m.bpm_const = m.bpm_drift <= 1.0
    # 4/4: kick regulat la tempo-ul piesei
    kick_ok = float(np.mean(kicks[pulse_w])) if pulse_w.any() else 0.0
    m.four = "da" if (m.pulse >= 60 and kick_ok > 0.30) else ("probabil" if m.pulse >= 30 else "nu")
    m.drums = "da" if m.pulse >= 50 else ("puțin" if m.pulse >= 20 else "nu")
    # intro / outro ritmic: ferestre cu puls de la început / de la sfârșit
    def run_len(flags):
        n = 0
        for f in flags:
            if not f:
                break
            n += 1
        return n
    if len(pulse_w):
        r_in, r_out = run_len(pulse_w), run_len(pulse_w[::-1])
        m.intro_s = int(min(m.duration, r_in * HOP + (WIN - HOP if r_in else 0)))
        m.outro_s = int(min(m.duration, r_out * HOP + (WIN - HOP if r_out else 0)))
        # cel mai lung segment cu puls (un break scurt, până la ~16 s, nu-l întrerupe)
        flags = list(pulse_w)
        idx = [i for i, f in enumerate(flags) if f]
        best = (0, 0)
        if idx:
            run_s = prev = idx[0]
            for i in idx[1:] + [None]:
                if i is None or i - prev > 4:
                    if prev - run_s > best[1] - best[0] or best == (0, 0):
                        best = (run_s, prev)
                    if i is not None:
                        run_s = i
                if i is not None:
                    prev = i
            m.seg_start = round(best[0] * HOP, 1)
            m.seg_end = round(min(m.duration, best[1] * HOP + WIN), 1)
    # drop urmat de puls
    if drops:
        ok = []
        for d in drops:
            idx = [i for i, s0 in enumerate(starts) if d <= s0 / fps <= d + 16]
            ok.append(bool(idx) and bool(np.mean(pulse_w[idx]) >= 0.5))
        m.drop_pulse = "da" if any(ok) else "nu"
    else:
        m.drop_pulse = "—"
    # voce: energie în zona vocii, care pulsează ca vorbirea/cântul (3–8 Hz), nu un pad continuu
    vr = float(np.median(vocal / total))
    env = vocal / (vocal.mean() + 1e-9)
    spec = np.abs(np.fft.rfft(env - env.mean()))
    mf = np.fft.rfftfreq(len(env), 1 / fps)
    band = (mf > 2.5) & (mf < 8)
    mod = float(spec[band].sum() / (spec[(mf > 0.2) & (mf < 20)].sum() + 1e-9))
    bass_ratio = float(np.median(low / total))
    v = np.clip((vr - 0.30) / 0.35, 0, 1) * np.clip(mod / 0.45, 0, 1)
    if m.drums == "da":
        v *= 0.85            # cu tobe, energia din zona vocii e și din instrumente
    m.voice = int(round(100 * v))
    named = bool(ACAPELLA_WORDS.search(name or ""))
    if named and re.search(r"a ?cap+el+a", name or "", re.I):
        m.kind = "acapella"
    elif m.drums == "nu" and bass_ratio < 0.10 and m.voice >= 55:
        m.kind = "acapella"
    elif m.voice >= 50 and m.drums != "da":
        m.kind = "vocal + puțin instrumental"
    elif m.voice < 25:
        m.kind = "instrumental"
    else:
        m.kind = "complet"
    # scor și motive
    reasons, sc = [], 0.0
    sc += m.pulse / 100 * 4
    if m.bpm_const:
        sc += 2
        reasons.append(f"beat stabil la {m.bpm:g}")
    elif m.pulse >= 30:
        reasons.append(f"tempo variabil (±{m.bpm_drift:g})")
    sc += {"da": 1.5, "probabil": 0.7}.get(m.four, 0)
    sc += min(m.intro_s, 16) / 16 + min(m.outro_s, 16) / 16
    if m.intro_s >= 8:
        reasons.append(f"intro ritmic de {m.intro_s}s")
    elif m.pulse >= 30:
        reasons.append("începe fără beat")
    if m.outro_s >= 8:
        reasons.append(f"outro ritmic de {m.outro_s}s")
    if m.drop_pulse == "da":
        sc += 0.5
    if m.pulse < 20:
        reasons.append("aproape fără puls")
    m.score = int(np.clip(round(sc), 1, 10))
    seg_len = m.seg_end - m.seg_start
    if m.kind == "acapella" or (m.kind.startswith("vocal") and m.pulse < 40):
        m.use = "overlay"
    elif m.pulse < 20:
        m.use = "intro / outro de set"
    elif m.bpm and m.bpm < 90 and m.bpm_const:
        m.use = "½ tempo"
    elif m.score >= 7 and (seg_len >= 0.7 * m.duration or (m.intro_s >= 8 and m.outro_s >= 8 and m.pulse >= 70)):
        m.use = "direct"
    elif seg_len >= 60:
        m.use = "segment"
    else:
        m.use = "greu de mixat"
    m.reasons = ", ".join(reasons)
    return m


def analyze(path: str, cache_dir: str, analysis_dir: str, force: bool = False) -> tuple[MixInfo, object]:
    """Mixabilitatea unei piese (cu cache). Face și analiza de bază (BPM, cheie, waveform) dacă lipsește,
    decodând fișierul o singură dată. Întoarce (MixInfo, Analysis)."""
    if not force:
        m = load_cached(path, cache_dir)
        if m is not None:
            return m, an.load_cached(path, analysis_dir)
    base = None if force else an.load_cached(path, analysis_dir)
    try:
        x = an.decode(path)
        if base is None or base.error:
            base = an.analyze_samples(x)
            an.save_cached(path, analysis_dir, base)
        m = analyze_samples(x, an.SR, base.bpm, base.key, base.energy, base.drops, os.path.basename(path))
    except Exception as e:  # noqa: BLE001
        from datetime import datetime
        m = MixInfo(error=str(e) or e.__class__.__name__, analyzed_at=datetime.now().isoformat(timespec="minutes"))
    save_cached(path, cache_dir, m)
    return m, base


def name_is_vocal(text: str) -> bool:
    """Acapella / vocal după nume sau taguri (sigur și instant)."""
    return bool(ACAPELLA_WORDS.search(text or ""))
