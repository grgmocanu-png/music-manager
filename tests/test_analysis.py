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
