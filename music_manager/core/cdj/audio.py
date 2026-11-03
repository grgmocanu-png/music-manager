"""Ce calculăm noi pentru stickul CDJ: waveform-ul (3 benzi) și beatgrid-ul (unde cade prima bătaie).

BPM-ul vine din Bibliotecă (Traktor / Rekordbox / analiza noastră). Gridul exact se ia din Traktor sau
Rekordbox când piesa e acolo; altfel îl calculăm aici, din atacurile din sunet.
"""
from __future__ import annotations

import numpy as np

from .pioneer.model import WaveformData

SR = 22050                  # rata la care decodăm pentru stick (ca în cratedrop)
BINS_PER_SECOND = 150       # rezoluția waveform-ului Pioneer
SAMPLES_PER_BIN = round(SR / BINS_PER_SECOND)      # 147
LOW_HZ, HIGH_HZ = 105.0, 980.0                      # benzile Pioneer: bas / mediu / înalt
BAND_P95_TARGET = 0.20
ONSET_BIAS_S = 0.0044       # atacul e detectat cu ~4 ms înainte de lovitură (măsurat pe piese de test)


def waveform(x: np.ndarray, sr: int = SR) -> WaveformData:
    """Amplitudine și energie pe 3 benzi, câte 150 de valori pe secundă (fără scipy: benzile din spectru)."""
    n_bins = len(x) // SAMPLES_PER_BIN
    if n_bins == 0:
        raise ValueError("piesa e prea scurtă")
    x = x[:n_bins * SAMPLES_PER_BIN].astype(np.float32)
    frames = x.reshape(n_bins, SAMPLES_PER_BIN)
    peak = np.abs(frames).max(axis=1)
    rms = np.sqrt((frames * frames).mean(axis=1))
    n_fft = 1024
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    sel = [(freqs < LOW_HZ), (freqs >= LOW_HZ) & (freqs < HIGH_HZ), (freqs >= HIGH_HZ)]
    pad = np.pad(x, (n_fft // 2, n_fft))
    bands = np.zeros((3, n_bins), dtype=np.float32)
    chunk = 2048
    for start in range(0, n_bins, chunk):
        stop = min(n_bins, start + chunk)
        idx = (np.arange(start, stop) * SAMPLES_PER_BIN + SAMPLES_PER_BIN // 2)[:, None] + np.arange(n_fft)[None, :]
        power = np.abs(np.fft.rfft(pad[idx] * win, axis=1)) ** 2
        for b, s in enumerate(sel):
            bands[b, start:stop] = np.sqrt(power[:, s].sum(axis=1))
    out = []
    for v in bands:
        p95 = float(np.percentile(v, 95)) if len(v) else 0.0
        out.append(v * (BAND_P95_TARGET / p95) if p95 > 0 else v)
    return WaveformData(bins_per_second=BINS_PER_SECOND, peak=tuple(peak.tolist()), rms=tuple(rms.tolist()),
                        low=tuple(out[0].tolist()), mid=tuple(out[1].tolist()), high=tuple(out[2].tolist()))


def _onsets(x: np.ndarray, sr: int, hop: int = 64, n_fft: int = 1024, lo: float = 30, hi: float = 160):
    """Atacurile din bas (kick-ul cade pe bătaie; hi-hat-ul și basul de pe contratimp nu), cu timpul fiecărui
    cadru în secunde."""
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    sel = (freqs > lo) & (freqs < hi)
    n = 1 + max(0, (len(x) - n_fft) // hop)
    env = np.zeros(n, dtype=np.float32)
    prev = None
    chunk = 4096
    for start in range(0, n, chunk):
        stop = min(n, start + chunk)
        idx = np.arange(start, stop)[:, None] * hop + np.arange(n_fft)[None, :]
        m = np.abs(np.fft.rfft(x[idx] * win, axis=1))[:, sel]      # liniar: kick-ul (puternic) bate basul
        d = np.diff(np.vstack([m[:1] if prev is None else prev[None, :], m]), axis=0)
        env[start:stop] = np.maximum(0, d).sum(axis=1)
        prev = m[-1]
    times = (np.arange(n) * hop + n_fft / 2) / sr      # centrul ferestrei
    return env, times


def first_beat(x: np.ndarray, bpm: float, sr: int = SR) -> tuple[float, int]:
    """(prima bătaie în ms, a câta bătaie din măsură e: 1..4), pentru un BPM dat.
    Faza: unde lovesc cele mai multe atacuri pe toată piesa. Măsura: bătaia cu cel mai mult bas (kick-ul de pe 1)."""
    if bpm <= 0 or len(x) < sr * 4:
        return 0.0, 1
    env, times = _onsets(x, sr)
    period = 60.0 / bpm
    dt = times[1] - times[0] if len(times) > 1 else 1.0
    best, best_s = 0.0, -1.0
    for ph in np.arange(0, period, 0.002):
        pos = ph + np.arange(int((times[-1] - ph) / period)) * period
        s = float(np.interp(pos, times, env).sum()) if len(pos) else 0.0
        if s > best_s:
            best, best_s = ph, s
    # rafinare: centrul de greutate al atacurilor în jurul fazei găsite (±10 ms)
    beats = best + np.arange(int((times[-1] - best) / period)) * period
    offs = np.arange(-0.010, 0.0105, dt)
    prof = np.array([np.interp(beats + o, times, env).sum() for o in offs])
    if prof.sum() > 0:
        best = float(best + (offs * prof).sum() / prof.sum())
    best = (best + ONSET_BIAS_S) % period
    # bătaia 1: cea cu basul cel mai puternic din cele 4 poziții posibile
    low = _bass(x, sr)
    lt = np.arange(len(low)) * 512 / sr
    beats = best + np.arange(int((lt[-1] - best) / period)) * period if len(lt) else np.array([])
    vals = np.interp(beats, lt, low) if len(beats) else np.array([])
    sums = [float(vals[j::4].sum()) for j in range(4)] if len(vals) >= 8 else [1, 0, 0, 0]
    j = int(np.argmax(sums))                    # bătaia j (0..3) e începutul măsurii
    in_bar = ((-j) % 4) + 1                     # prima bătaie din piesă e a câta din măsură
    return round(best * 1000.0, 1), in_bar


def _bass(x: np.ndarray, sr: int) -> np.ndarray:
    n_fft, hop = 2048, 512
    win = np.hanning(n_fft).astype(np.float32)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    sel = (freqs > 30) & (freqs < 120)
    n = 1 + max(0, (len(x) - n_fft) // hop)
    out = np.zeros(n, dtype=np.float32)
    for start in range(0, n, 4096):
        stop = min(n, start + 4096)
        idx = np.arange(start, stop)[:, None] * hop + np.arange(n_fft)[None, :]
        out[start:stop] = np.abs(np.fft.rfft(x[idx] * win, axis=1))[:, sel].sum(axis=1)
    return out


def grid_from_anchor(anchor_ms: float, bpm: float, anchor_in_bar: int = 1) -> tuple[float, int]:
    """Gridul din Traktor / Rekordbox (un marcaj oriunde în piesă) → prima bătaie de la începutul piesei."""
    period = 60000.0 / bpm
    k = int(anchor_ms // period)
    first = anchor_ms - k * period
    return round(first, 1), ((anchor_in_bar - 1 - k) % 4) + 1
