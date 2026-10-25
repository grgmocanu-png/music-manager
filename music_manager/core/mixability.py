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
import sys
from dataclasses import asdict, dataclass

import numpy as np

from .safeio import write_json
from . import analysis as an

MIX_VERSION = 2          # 2: tempo precis la BPM mare, breakbeat, BPM din Bibliotecă
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
    write_json(p, asdict(m))


def move_cached(old: str, new: str, cache_dir: str) -> None:
    a, b = (os.path.join(cache_dir, _key(x) + ".json") for x in (old, new))
    try:
        if os.path.exists(a) and not os.path.exists(b):
            os.replace(a, b)
    except OSError:
        pass


# --- analiza -----------------------------------------------------------------
N_FFT, HOP_F = 1024, 128      # ~86 cadre / secundă la 11 kHz: precizie bună și la 170–200 BPM


def _features(x: np.ndarray, sr: int):
    """Atacurile (toată banda, kick, toba mică / hi-hat) și energiile, calculate pe bucăți (memorie mică)."""
    freqs = np.fft.rfftfreq(N_FFT, 1 / sr)
    fps = sr / HOP_F
    b_all = (freqs > 30) & (freqs < 5000)
    b_kick = (freqs > 30) & (freqs < 160)
    b_snare = (freqs > 1500) & (freqs < 5000)
    b_low = (freqs > 30) & (freqs < 150)
    b_voc = (freqs > 300) & (freqs < 3400)
    outs = {k: [] for k in ("onset", "kick", "snare", "total", "low", "vocal")}
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    n_frames = 1 + (len(x) - N_FFT) // HOP_F
    step = max(1, int(sr * 60) // HOP_F)          # ~1 minut de cadre pe bucată
    prev = None
    for f0 in range(0, n_frames, step):
        f1 = min(n_frames, f0 + step)
        chunk = x[f0 * HOP_F:(f1 - 1) * HOP_F + N_FFT]
        mag = an._stft_mag(chunk, N_FFT, HOP_F)[: f1 - f0]
        logm = np.log1p(10 * mag)
        first = logm[:1] if prev is None else prev
        flux = np.maximum(0, np.diff(np.vstack([first, logm]), axis=0))
        prev = logm[-1:]
        outs["onset"].append(flux[:, b_all].sum(axis=1))
        outs["kick"].append(flux[:, b_kick].sum(axis=1))
        outs["snare"].append(flux[:, b_snare].sum(axis=1))
        outs["total"].append(mag[:, b_all].sum(axis=1) + 1e-9)
        outs["low"].append(mag[:, b_low].sum(axis=1))
        outs["vocal"].append(mag[:, b_voc].sum(axis=1))
    o = {k: (np.concatenate(v) if v else np.zeros(1)) for k, v in outs.items()}
    return o["onset"], o["kick"], o["snare"], o["total"], o["low"], o["vocal"], fps


def _acf(env: np.ndarray):
    e = env - env.mean()
    n = len(e)
    if n < 8 or not np.any(e):
        return None
    # fără atacuri reale (ton continuu, pad, zgomot): nu căutăm puls în zgomotul de calcul
    p95, med = float(np.percentile(env, 95)), float(np.median(env))
    if p95 < 1.0 or p95 - med < 0.25 * med:
        return None
    f = np.fft.rfft(e, 2 * n)
    acf = np.fft.irfft(f * np.conj(f))[:n]
    return acf / (acf[0] + 1e-9)


def _peak_near(acf, lag: float, tol: float = 0.04):
    """Vârful autocorelației lângă un lag (±tol), cu interpolare parabolică. Întoarce (lag precis, valoare)."""
    if acf is None:
        return 0.0, 0.0
    lo, hi = int(max(1, np.floor(lag * (1 - tol)))), int(min(len(acf) - 2, np.ceil(lag * (1 + tol))))
    if hi <= lo:
        return 0.0, 0.0
    i = lo + int(np.argmax(acf[lo:hi + 1]))
    a, b, c = acf[i - 1], acf[i], acf[i + 1]
    d = a - 2 * b + c
    off = 0.5 * (a - c) / d if d < 0 else 0.0
    return float(i + off), float(max(0.0, b - 0.25 * (a - c) * off))


def _local_tempo(env: np.ndarray, fps: float, lo=60.0, hi=220.0):
    """(bpm, tărie 0..1) într-o fereastră, fără tempo de referință."""
    acf = _acf(env)
    if acf is None or len(env) < fps * 3:
        return 0.0, 0.0
    lags = np.arange(int(fps * 60 / hi), min(len(acf) - 2, int(fps * 60 / lo)) + 1)
    if len(lags) < 3:
        return 0.0, 0.0
    i = int(lags[int(np.argmax(acf[lags]))])
    lag, v = _peak_near(acf, i, 0.02)
    return (60 * fps / lag if lag else 0.0), v


def _at_tempo(env: np.ndarray, fps: float, bpm: float):
    """Cât de tare pulsează fereastra la tempo-ul dat (sau la dublu / jumătate) și tempo-ul local precis."""
    acf = _acf(env)
    if acf is None or not bpm:
        return 0.0, 0.0
    best_v, best_b = 0.0, 0.0
    for mult in (1.0, 2.0, 0.5):
        b = bpm * mult
        if not 55 <= b <= 230:
            continue
        lag, v = _peak_near(acf, 60 * fps / b)
        if lag and v > best_v + (0.0 if mult == 1.0 else 0.05):    # preferăm tempo-ul piesei
            best_v, best_b = v, 60 * fps / lag / mult
    return best_b, best_v


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
                    drops=(), name: str = "", _retry: bool = True) -> MixInfo:
    from datetime import datetime
    m = MixInfo(analyzed_at=datetime.now().isoformat(timespec="minutes"), key=key, energy=energy)
    m.duration = len(x) / sr
    if m.duration < 20:
        m.error = "piesă prea scurtă"
        return m
    onset, kick, snare, total, low, vocal, fps = _features(x, sr)
    if not bpm:
        bpm, _c = an.estimate_bpm(x, sr, 70, 180)
        # piese rapide (DnB, hardcore): estimarea poate ieși pe jumătate — verificăm dublul
        if bpm and bpm < 115:
            acf = _acf(onset[: int(fps * 90)])
            _l1, v1 = _peak_near(acf, 60 * fps / bpm)
            _l2, v2 = _peak_near(acf, 60 * fps / (bpm * 2))
            if v2 > v1 * 0.9 and bpm * 2 <= 230:
                bpm *= 2
    m.bpm = round(bpm, 2) if bpm else 0.0
    win, hop = int(WIN * fps), int(HOP * fps)
    starts = list(range(0, max(1, len(onset) - win), hop)) or [0]
    strengths, tempos, kicks = [], [], []
    for s0 in starts:
        if bpm:
            b, st = _at_tempo(onset[s0:s0 + win], fps, bpm)
            _b2, st2 = _at_tempo(snare[s0:s0 + win], fps, bpm)   # breakbeat: toba mică / hi-hat
            st = max(st, st2 * 0.9)
            _kb, kst = _at_tempo(kick[s0:s0 + win], fps, bpm)
        else:
            b, st = _local_tempo(onset[s0:s0 + win], fps)
            _kb, kst = _local_tempo(kick[s0:s0 + win], fps)
        strengths.append(st)
        tempos.append(_fold(b, bpm))
        kicks.append(kst)
    strengths, tempos, kicks = np.array(strengths), np.array(tempos), np.array(kicks)
    pulse_w = strengths > 0.22
    if bpm:
        pulse_w &= np.abs(tempos - bpm) < max(2.5, bpm * 0.025)
    m.pulse = int(round(100 * pulse_w.mean()))
    if _retry and bpm and m.pulse < 40:
        # BPM-ul primit poate fi greșit (ex. 100 în loc de 150): dacă piesa pulsează clar la alt tempo, îl folosim
        free = [(_local_tempo(onset[s0:s0 + win], fps)) for s0 in starts]
        good = [b for b, st in free if st > 0.22 and b]
        if len(good) >= 0.5 * len(starts):
            med = float(np.median(good))
            ratio = med / bpm
            if not any(abs(ratio - r) < 0.03 for r in (0.5, 1.0, 2.0)):
                m2 = analyze_samples(x, sr, round(med, 1), key, energy, drops, name, _retry=False)
                if m2.pulse > m.pulse + 20:
                    m2.reasons = (f"BPM {bpm:g} nu se potrivește, piesa e la ~{m2.bpm:g}; " + m2.reasons).strip("; ")
                    return m2
    if m.pulse < 10:
        m.bpm = 0.0          # fără puls, „BPM-ul” n-are sens
    # BPM constant: cât variază tempo-ul local pe porțiunile cu puls
    if pulse_w.sum() >= 3:
        t = tempos[pulse_w]
        m.bpm_drift = round(float(np.percentile(t, 90) - np.percentile(t, 10)) / 2, 1)
        m.bpm_const = m.bpm_drift <= 1.0
    # 4/4: kick regulat la tempo-ul piesei
    kick_ok = float(np.mean(kicks[pulse_w])) if pulse_w.any() else 0.0
    m.four = "da" if (m.pulse >= 60 and kick_ok > 0.30) else ("probabil" if (m.pulse >= 30 and kick_ok > 0.18)
                                                              else "nu")
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
    if m.four == "nu" and m.pulse >= 60 and m.bpm_const:
        sc += 1.2                       # breakbeat stabil (DnB, jungle, breaks): se mixează bine și fără 4/4
        reasons.append("breakbeat stabil")
    else:
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


def analyze(path: str, cache_dir: str, analysis_dir: str, force: bool = False,
            ref_bpm: float = 0.0) -> tuple[MixInfo, object]:
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
        # BPM-ul de referință: cel din Bibliotecă (Traktor / Rekordbox / pus de tine), altfel din analiză
        m = analyze_samples(x, an.SR, ref_bpm or base.bpm, base.key, base.energy, base.drops,
                            os.path.basename(path))
    except Exception as e:  # noqa: BLE001
        from datetime import datetime
        m = MixInfo(error=str(e) or e.__class__.__name__, analyzed_at=datetime.now().isoformat(timespec="minutes"))
    save_cached(path, cache_dir, m)
    return m, base


def name_is_vocal(text: str) -> bool:
    """Acapella / vocal după nume sau taguri (sigur și instant)."""
    return bool(ACAPELLA_WORDS.search(text or ""))


# --- analiză în procese separate (interfața nu se mai blochează) -------------------------
def _low_priority():
    """Procesele de analiză au prioritate mică: calculatorul și aplicația rămân fluide."""
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        else:
            os.nice(10)
    except Exception:  # noqa: BLE001
        pass


def analyze_to_cache(path: str, cache_dir: str, analysis_dir: str, force: bool = False,
                     ref_bpm: float = 0.0) -> str:
    """Pentru procesele de analiză: rezultatul rămâne în cache, întoarce doar calea."""
    if os.path.exists(path):
        analyze(path, cache_dir, analysis_dir, force=force, ref_bpm=ref_bpm)
    return path


def analyze_many(items, cache_dir: str, analysis_dir: str, force: bool = False,
                 progress=None, cancelled=None, workers: int | None = None) -> int:
    """Analizează mai multe piese în procese separate (1-2), cu fir de rezervă dacă procesele nu pornesc.
    items = [(cale, ref_bpm)]; progress(i, total, cale) după fiecare piesă."""
    items = list(items)
    total = len(items)
    progress = progress or (lambda *a: None)
    cancelled = cancelled or (lambda: False)
    done: set[str] = set()
    n = 0
    if workers is None:
        workers = max(1, min(2, (os.cpu_count() or 2) - 1))
    if total >= 1 and workers > 0:
        try:
            import multiprocessing as mp
            from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
            ex = ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"),
                                     initializer=_low_priority)
            try:
                pending = {ex.submit(analyze_to_cache, p, cache_dir, analysis_dir, force, b): p for p, b in items}
                while pending:
                    if cancelled():
                        for f in pending:
                            f.cancel()
                        break
                    fin, _ = wait(list(pending), timeout=0.5, return_when=FIRST_COMPLETED)
                    for f in fin:
                        p = pending.pop(f)
                        f.result()          # BrokenProcessPool → trecem pe fir
                        done.add(p)
                        n += 1
                        progress(n, total, p)
            finally:
                ex.shutdown(wait=not cancelled(), cancel_futures=True)
        except Exception:  # noqa: BLE001 — procesele n-au mers: continuăm în acest fir
            pass
    for p, b in items:
        if cancelled():
            break
        if p in done:
            continue
        analyze_to_cache(p, cache_dir, analysis_dir, force, b)
        done.add(p)
        n += 1
        progress(n, total, p)
    return n
