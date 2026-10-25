import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from synth import make  # noqa: E402

from music_manager.core import analysis as an  # noqa: E402


def test_bpm_key_drop(tmp_path):
    cases = [(128, (57, 60, 64), 45, "Am"), (124, (54, 58, 61), 42, "F#"), (174, (62, 65, 69), 38, "Dm")]
    for bpm, chord, bass, key in cases:
        p = str(tmp_path / f"t{bpm}.wav")
        make(p, bpm=bpm, key_notes=chord, bass_root=bass, dur=120, brk=(50, 75))
        a = an.analyze(p, cache_dir=str(tmp_path / "cache"))
        assert not a.error, a.error
        assert abs(a.bpm - bpm) <= 0.5, (bpm, a.bpm)
        assert a.key == key, (key, a.key)
        assert any(abs(d - 75) <= 3 for d in a.drops), a.drops
        assert len(a.peak) == an.WAVE_BINS and max(a.low) == 255
        # a doua oară vine din cache
        assert an.load_cached(p, str(tmp_path / "cache")).bpm == a.bpm


def test_cache_failures_dates_and_drive_change(tmp_path):
    import shutil
    cache = str(tmp_path / "cache")
    bad = tmp_path / "bad.mp3"
    bad.write_bytes(b"not audio" * 50)
    a = an.analyze(str(bad), cache)
    assert a.error and a.analyzed_at
    # eșecul e ținut minte: nu se reîncearcă singur
    again = an.load_cached(str(bad), cache)
    assert again is not None and again.error
    assert an.cached_date(str(bad), cache)
    # analiza o găsim și când fișierul apare la altă cale (ex. discul a primit altă literă)
    p = str(tmp_path / "d1" / "Artist - Song.wav")
    os.makedirs(os.path.dirname(p))
    make(p, bpm=125, key_notes=(57, 60, 64), bass_root=45, dur=30, brk=(10, 20))
    first = an.analyze(p, cache)
    q = str(tmp_path / "d2" / "Artist - Song.wav")
    os.makedirs(os.path.dirname(q))
    shutil.copy(p, q)
    got = an.load_cached(q, cache)
    assert got is not None and got.bpm == first.bpm
    # force reanalizează
    assert an.analyze(str(bad), cache, force=True).error


def test_mixability(tmp_path):
    import numpy as np
    from music_manager.core import mixability as mx
    p = str(tmp_path / "m.wav")
    make(p, bpm=124, key_notes=(57, 60, 64), bass_root=45, dur=90, brk=(40, 55))
    m, a = mx.analyze(p, str(tmp_path / "mix"), str(tmp_path / "an"))
    assert not m.error and m.bpm_const and m.four == "da" and m.pulse >= 70 and m.score >= 7
    assert m.use == "direct" and m.seg_end - m.seg_start > 60 and a is not None and a.bpm
    assert mx.load_cached(p, str(tmp_path / "mix")).score == m.score
    t = np.arange(an.SR * 60) / an.SR
    pad = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    q = mx.analyze_samples(pad, an.SR, name="Song (Acapella).wav")
    assert q.pulse < 20 and q.kind == "acapella" and q.score <= 3 and q.bpm == 0
    assert mx.name_is_vocal("Les Fleurs (acapella)") and not mx.name_is_vocal("Moody")


def test_analyze_many_in_processes(tmp_path):
    """v0.9.8: analiza din Explore rulează în procese separate (cu fir de rezervă)."""
    from music_manager.core import mixability as mx
    paths = []
    for i, b in enumerate((122, 128)):
        p = str(tmp_path / f"t{i}.wav")
        make(p, bpm=b, key_notes=(57, 60, 64), bass_root=45, dur=30, brk=(10, 20))
        paths.append(p)
    md, ad = str(tmp_path / "mix"), str(tmp_path / "an")
    seen = []
    n = mx.analyze_many([(p, 0.0) for p in paths], md, ad, progress=lambda i, t, p: seen.append(p), workers=2)
    assert n == 2 and sorted(seen) == sorted(paths)
    assert all(mx.load_cached(p, md) is not None for p in paths)
    # fără procese (workers=0) merge pe fir; anularea oprește tot
    seen.clear()
    assert mx.analyze_many([(p, 0.0) for p in paths], md, ad, force=True, workers=0,
                           progress=lambda i, t, p: seen.append(p)) == 2
    assert mx.analyze_many([(p, 0.0) for p in paths], md, ad, cancelled=lambda: True, workers=0) == 0
