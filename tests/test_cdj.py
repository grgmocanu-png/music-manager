"""Stick CDJ (export.pdb + ANLZ), scris într-un folder care ține locul stickului."""
import glob
import os
import struct
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from synth import make  # noqa: E402

from music_manager.core import analysis as an  # noqa: E402
from music_manager.core.cdj import audio as ca  # noqa: E402
from music_manager.core.cdj import drives as drv  # noqa: E402
from music_manager.core.cdj import stick  # noqa: E402

GOLDEN_TAGS = {"DAT": ["PPTH", "PVBR", "PQTZ", "PWAV", "PWV2", "PCOB", "PCOB"],
               "EXT": ["PPTH", "PWV3", "PCOB", "PCOB", "PCO2", "PCO2", "PQT2", "PWV5", "PWV4"],
               "2EX": ["PPTH", "PWV7", "PWV6", "PWVC"]}     # ca într-un export real Rekordbox 7


def _tags(path):
    """{secțiune: octeți} pentru un fișier ANLZ."""
    b = open(path, "rb").read()
    i = struct.unpack(">I", b[4:8])[0]
    out = {}
    while i < len(b):
        n = struct.unpack(">I", b[i + 8:i + 12])[0]
        out.setdefault(b[i:i + 4].decode(), b[i:i + n])
        i += n
    return out


def _order(b):
    i = struct.unpack(">I", b[4:8])[0]
    out = []
    while i < len(b):
        out.append(b[i:i + 4].decode())
        i += struct.unpack(">I", b[i + 8:i + 12])[0]
    return out


def _first_beat_ms(dat_path):
    """Prima intrare din PQTZ (beatgrid): (bătaia din măsură, BPM, ms)."""
    t = _tags(dat_path)["PQTZ"]
    hdr = struct.unpack(">I", t[4:8])[0]
    beat, tempo, ms = struct.unpack(">HHI", t[hdr:hdr + 8])
    return beat, tempo / 100, ms


@pytest.fixture(scope="module")
def songs(tmp_path_factory):
    d = tmp_path_factory.mktemp("hard")
    out = []
    for i, b in enumerate((124, 128, 122)):
        p = str(d / f"Artist {i} - Piesă {i}.wav")
        make(p, bpm=b, dur=40, brk=(15, 20))
        out.append((p, b))
    return out


def _t(p, b, **k):
    return stick.StickTrack(src=p, usb="", artist=os.path.basename(p).split(" - ")[0],
                            title=os.path.basename(p)[:-4], bpm=b, key="8A", **k)


def test_write_sync_prune(songs, tmp_path):
    usb = str(tmp_path / "USB")
    os.makedirs(usb)
    pls = [("Warm up", [_t(*songs[0]), _t(*songs[1])]),
           ("Peak ăî", [_t(*songs[1]), _t(*songs[2], grid_ms=1234.5, grid_bpm=122.0, grid_beat=1)])]
    p = stick.plan(usb, pls)
    assert len(p.tracks) == 3 and len(p.copy) == 3 and not os.listdir(usb)      # previzualizarea nu scrie nimic
    rep = stick.write(p, cache_dir=str(tmp_path / "cache"))
    assert rep["copied"] == 3 and not rep["errors"]
    back = stick.read_back(usb)
    assert [t[2] for t in back["tracks"]] == [124.0, 128.0, 122.0]
    assert all(t[3] == "Am" for t in back["tracks"])
    assert back["playlists"] == {"Warm up": ["Artist 0 - Piesă 0", "Artist 1 - Piesă 1"],
                                 "Peak ăî": ["Artist 1 - Piesă 1", "Artist 2 - Piesă 2"]}
    dats = sorted(glob.glob(os.path.join(usb, "PIONEER", "USBANLZ", "*", "*", "ANLZ0000.DAT")))
    assert len(dats) == 3
    for dat in dats:
        for ext, want in GOLDEN_TAGS.items():
            assert _order(open(dat[:-3] + ext, "rb").read()) == want
    beats = sorted(_first_beat_ms(d) for d in dats)
    # gridul din Traktor (marcaj la 1234,5 ms, 122 BPM) → prima bătaie la 1234,5 − 2 × 491,8 = 250,9 ms
    assert any(abs(ms - 251) <= 1 and bpm == 122.0 for _b, bpm, ms in beats)
    # a doua oară: nimic de copiat
    p2 = stick.plan(usb, pls)
    assert not p2.copy and p2.unchanged == 3
    # piesa scoasă din playlisturi se șterge doar cu bifă, și doar dacă a pus-o Manager
    open(os.path.join(usb, "Contents", "al meu.txt"), "w").write("x")
    assert not stick.plan(usb, pls[:1]).delete
    p3 = stick.plan(usb, pls[:1], prune=True)
    assert p3.delete == ["/Contents/Artist 2/Artist 2 - Piesă 2.wav"]
    stick.write(p3, cache_dir=str(tmp_path / "cache"))
    assert len(stick.read_back(usb)["tracks"]) == 2
    assert os.path.exists(os.path.join(usb, "Contents", "al meu.txt"))
    assert len(glob.glob(os.path.join(usb, "PIONEER", "USBANLZ", "*", "*", "ANLZ0000.DAT"))) == 2


def test_first_beat_and_waveform(songs):
    p, bpm = songs[0]
    x = an.decode(p, sr=ca.SR)
    pad = 0.337
    x = np.concatenate([np.zeros(int(pad * ca.SR), np.float32), x])
    ms, _bar = ca.first_beat(x, bpm)
    assert abs(ms - pad * 1000) < 5
    w = ca.waveform(x)
    assert len(w.peak) == len(x) // ca.SAMPLES_PER_BIN and max(w.low) > 0
    assert ca.grid_from_anchor(1234.5, 122.0, 1) == (250.9, 3)


def test_drive_checks(tmp_path):
    d = drv.Drive(str(tmp_path), "DJ", "FAT32", 16 * 1024 ** 3, 8 * 1024 ** 3)
    assert drv.check(d)[0]                                          # gol
    (tmp_path / "poze.jpg").write_bytes(b"x")
    ok, why = drv.check(d)
    assert not ok and "formatează" in why                          # alte fișiere, nu e stick de DJ
    (tmp_path / "PIONEER").mkdir()
    assert drv.check(d)[0]                                          # stick de DJ
    assert not drv.check(drv.Drive(str(tmp_path), "X", "NTFS", 1, 1))[0]
    assert drv.clean_label("Stick dj ăî 2026!") == "STICKDJ2026"
    if sys.platform == "win32":
        assert not drv.can_format(drv.Drive("E:\\", "X", "exFAT", 64 * 1024 ** 3, 1))[0]
        assert drv.can_format(drv.Drive("E:\\", "X", "exFAT", 29 * 1024 ** 3, 1))[0]
    drv.list_drives()                                               # nu crapă (în CI nu e niciun stick)
