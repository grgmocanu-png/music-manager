"""Dubluri după sunet (Chromaprint)."""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from music_manager.core import fingerprint as fpr  # noqa: E402
from music_manager.core.library import Track, find_duplicates  # noqa: E402


def _noisy(a, rng, flips=0.03):
    b = a.copy()
    mask = (rng.random((len(a), 32)) < flips)
    bits = (mask * (1 << np.arange(32, dtype=np.uint64))).sum(axis=1).astype(np.uint32)
    return b ^ bits


def test_similarity_and_groups():
    rng = np.random.default_rng(1)
    a = rng.integers(0, 2**32, 900, dtype=np.uint64).astype(np.uint32)
    b = _noisy(a[7:], rng)                     # același sunet, re-encodat, început tăiat
    c = rng.integers(0, 2**32, 900, dtype=np.uint64).astype(np.uint32)
    d = _noisy(a, rng)                         # același început, dar piesa e mult mai lungă (Extended)
    assert fpr.similarity(a, b) > 0.9
    assert fpr.similarity(a, c) < 0.6
    fps = {"a.mp3": a, "b.flac": b, "c.mp3": c, "d.mp3": d}
    durs = {"a.mp3": 210, "b.flac": 209, "c.mp3": 300, "d.mp3": 420}
    m = fpr.sound_matches(fps, durs)
    pairs = {frozenset((x.a, x.b)): x for x in m}
    assert frozenset(("a.mp3", "b.flac")) in pairs and not pairs[frozenset(("a.mp3", "b.flac"))].version
    assert pairs[frozenset(("a.mp3", "d.mp3"))].version
    assert not any("c.mp3" in p for p in pairs)
    tracks = [Track(path=p, artist="X", title=f"T{i}", duration=durs[p], bitrate=320) for i, p in enumerate(fps)]
    tracks.append(Track(path="e.mp3", artist="Y", title="Z", duration=200, bitrate=320))
    tracks.append(Track(path="f.mp3", artist="Y", title="Z", duration=201, bitrate=128))
    groups = fpr.merge_groups(find_duplicates(tracks), tracks, m)
    by = {frozenset(t.path for t in g): info for g, info in groups}
    abd = by[frozenset(("a.mp3", "b.flac", "d.mp3"))]
    assert abd.version and abd.reason["b.flac"].startswith("sunet")
    ef = by[frozenset(("e.mp3", "f.mp3"))]
    assert not ef.version and ef.reason["e.mp3"] == "taguri"


@pytest.mark.skipif(not (shutil.which("fpcalc") or fpr.fpcalc_path()), reason="fpcalc lipsește")
def test_fpcalc_real(tmp_path):
    from synth import make
    from music_manager.core.convert import ffmpeg_path
    w = str(tmp_path / "a.wav")
    make(w, bpm=126, dur=60, brk=(20, 30))
    mp3 = str(tmp_path / "a.mp3")
    subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-i", w, "-b:a", "128k", mp3], check=True)
    other = str(tmp_path / "b.wav")
    make(other, bpm=100, key_notes=(50, 53, 57), bass_root=38, dur=60, brk=(30, 40))
    exe = fpr.fpcalc_path()
    fps, errors = fpr.compute_many([w, mp3, other], exe, str(tmp_path / "cache"))
    assert not errors and len(fps) == 3
    assert fpr.similarity(fps[w], fps[mp3]) > fpr.SAME
    assert fpr.similarity(fps[w], fps[other]) < fpr.SAME
    assert fpr.load_cached(str(tmp_path / "cache"), w) is not None
