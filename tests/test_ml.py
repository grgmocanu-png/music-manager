"""Analize avansate (modele descărcate la cerere): părțile care nu au nevoie de modele."""
import io
import os

import numpy as np

from music_manager.core import ml


def test_peaks_and_grid(monkeypatch):
    fps = ml.BEAT_FPS
    n = fps * 60
    logits = np.full(n, -5.0, np.float32)
    period = 60 / 124
    beats = 0.337 + np.arange(int((60 - 0.4) / period)) * period
    for i, b in enumerate(beats):
        f = int(round(b * fps))
        logits[f] = 3.0
        logits[f + 1] = 2.9 if i % 3 == 0 else -5           # vârf lipit de vecin → se unesc
    p = ml._peaks(logits)
    assert len(p) == len(beats) and np.abs(p - beats).max() < 0.03
    down = beats[2::4]
    monkeypatch.setattr(ml, "beats", lambda x, folder="": (beats, down))
    g = ml.grid(np.zeros(10), "")
    assert g["bpm"] == 124.0 and abs(g["first_ms"] - 337) < 2 and g["confidence"] > 0.9
    assert g["in_bar"] == 3                                  # downbeat-ul e a treia bătaie din piesă


def test_genre_input_and_keys():
    x = np.random.default_rng(0).standard_normal(16000 * 20).astype(np.float32) * 0.1
    patches = ml.genre_input(x)
    assert patches.shape[1:] == (128, 96) and len(patches) >= 15 and np.isfinite(patches).all()
    assert ml._key_short("A minor") == "Am" and ml._key_short("Bb Major") == "A#"
    assert ml._key_short("F# minor") == "F#m"


def test_download_with_fallback(tmp_path):
    calls = []

    class R(io.BytesIO):
        headers = {"Content-Length": "3"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def http_open(url):
        calls.append(url)
        if "essentia" in url:
            raise OSError("site căzut")
        return R(b"abc")
    ml.download("genre", str(tmp_path), http_open=http_open)
    assert all(os.path.getsize(tmp_path / f) == 3 for f in ml.MODELS["genre"]["files"])
    assert any("huggingface" in c for c in calls)
    ml.remove("genre", str(tmp_path))
    assert not os.listdir(tmp_path)
