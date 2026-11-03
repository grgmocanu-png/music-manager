"""Quantize neutral WaveformData into Pioneer waveform byte arrays.

WaveformData carries per-bin values at a fixed rate (150 bins/s — the same
resolution as Pioneer scroll waveforms): peak and rms in 0..1, plus low/mid/
high band energies with EMA-band semantics matching the DjManager reference
(bass ≈ <105 Hz, mid ≈ 105–980 Hz, treble ≈ >980 Hz). The scale factors here
are calibrated against golden Rekordbox ANLZ output for the same audio
(band gains: ×48 into 3-bit channels, ×330 into 8-bit; heights ×53).
"""

from __future__ import annotations

import numpy as np

from .model import WaveformData
from .sections import PWAV_COLS, PWV2_COLS, PWV4_COLS, PWV6_COLS

SCROLL_BINS_PER_SECOND = 150


def _resample(values: np.ndarray, num_cols: int, reduce: str = "mean") -> np.ndarray:
    """Downsample per-bin values to num_cols fixed columns."""
    n = len(values)
    edges = (np.arange(num_cols + 1) * n) // num_cols
    edges = np.maximum.accumulate(np.minimum(edges, n))
    out = np.empty(num_cols)
    for i in range(num_cols):
        lo, hi = edges[i], max(edges[i + 1], edges[i] + 1)
        chunk = values[lo:hi] if lo < n else values[-1:]
        out[i] = chunk.max() if reduce == "max" else chunk.mean()
    return out


def _arrays(w: WaveformData) -> tuple[np.ndarray, ...]:
    return tuple(np.asarray(x, dtype=np.float64)
                 for x in (w.peak, w.rms, w.low, w.mid, w.high))


def _height(rms: np.ndarray) -> np.ndarray:
    """Transfer curve fitted per-bin against golden PWV3 heights over 216k
    bins / 6 tracks of the user's own Rekordbox-analyzed library."""
    return np.minimum(31, np.round(51.0 * np.power(np.maximum(rms, 0.0), 1.072))).astype(np.uint16)


def _mono_byte(rms: np.ndarray, peak: np.ndarray) -> np.ndarray:
    """(whiteness[0-7] << 5) | height[0-31] — shared by PWV3 and PWAV."""
    with np.errstate(divide="ignore", invalid="ignore"):
        transient = np.where(rms > 0.001, np.minimum(peak / (rms + 0.001), 8.0), 0.0)
    # whiteness fitted to golden: quiet/steady bins get zero, only real
    # transients whiten (the old 2x-everywhere gain read as "messy" on deck)
    whiteness = np.clip(np.round(3.21 * transient - 3.88), 0, 7).astype(np.uint16)
    return ((whiteness & 7) << 5) | (_height(rms) & 31)


def pwv3_bytes(w: WaveformData) -> bytes:
    peak, rms, *_ = _arrays(w)
    return _mono_byte(rms, peak).astype(np.uint8).tobytes()


def pwv5_bytes(w: WaveformData) -> bytes:
    peak, rms, low, mid, high = _arrays(w)
    height = _height(rms)
    r = np.minimum(7, np.round(high * 48)).astype(np.uint16)
    g = np.minimum(7, np.round(mid * 48)).astype(np.uint16)
    b = np.minimum(7, np.round(low * 48)).astype(np.uint16)
    words = ((r & 7) << 13) | ((g & 7) << 10) | ((b & 7) << 7) | ((height & 31) << 2)
    return words.astype(">u2").tobytes()


def pwv7_bytes(w: WaveformData) -> bytes:
    _, _, low, mid, high = _arrays(w)
    cols = np.stack([
        np.minimum(255, np.round(high * 330)),
        np.minimum(255, np.round(mid * 330)),
        np.minimum(255, np.round(low * 330)),
    ], axis=1)
    return cols.astype(np.uint8).tobytes()


def pwav_bytes(w: WaveformData) -> bytes:
    peak, rms, *_ = _arrays(w)
    rms_c = _resample(rms, PWAV_COLS)
    peak_c = _resample(peak, PWAV_COLS, reduce="max")
    return _mono_byte(rms_c, peak_c).astype(np.uint8).tobytes()


def pwv2_bytes(w: WaveformData) -> bytes:
    _, rms, *_ = _arrays(w)
    rms_c = _resample(rms, PWV2_COLS)
    return (np.minimum(15, np.round(rms_c * 31)).astype(np.uint8) & 0x0F).tobytes()


def pwv4_bytes(w: WaveformData) -> bytes:
    peak, rms, low, mid, high = _arrays(w)
    p = np.minimum(255, np.round(_resample(peak, PWV4_COLS, "max") * 255))
    cols = np.stack([
        p,
        255 - p,
        np.minimum(255, np.round(_resample(rms, PWV4_COLS) * 330)),
        np.minimum(255, np.round(_resample(low, PWV4_COLS) * 330)),
        np.minimum(255, np.round(_resample(mid, PWV4_COLS) * 330)),
        np.minimum(255, np.round(_resample(high, PWV4_COLS) * 330)),
    ], axis=1)
    return cols.astype(np.uint8).tobytes()


def pwv6_bytes(w: WaveformData) -> bytes:
    _, _, low, mid, high = _arrays(w)
    cols = np.stack([
        np.minimum(255, np.round(_resample(high, PWV6_COLS) * 330)),
        np.minimum(255, np.round(_resample(mid, PWV6_COLS) * 330)),
        np.minimum(255, np.round(_resample(low, PWV6_COLS) * 330)),
    ], axis=1)
    return cols.astype(np.uint8).tobytes()
