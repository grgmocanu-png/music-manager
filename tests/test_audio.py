"""Teste cu fișiere audio reale, generate cu ffmpeg-ul inclus."""
import os
import subprocess

import pytest

from music_manager.core import convert as conv
from music_manager.core import tags as tagmod
from music_manager.core.library import LibraryCache, find_duplicates, scan

FFMPEG = conv.ffmpeg_path()
COVER = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")


def make(path, fmt_args, dur=2):
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:duration={dur}", *fmt_args, str(path)], check=True)
    return str(path)


@pytest.fixture
def flac(tmp_path):
    (tmp_path / "src").mkdir()
    p = make(tmp_path / "src" / "a.flac", ["-ar", "96000", "-sample_fmt", "s32", "-c:a", "flac"])
    tagmod.write_tags(p, {"artist": "Ana Ș", "title": "Dor", "album": "Alb", "genre": "House",
                          "year": "2021-03-04", "track": "3/10", "bpm": "124.0", "key": "8A"}, COVER)
    return p


def test_tag_roundtrip_all_formats(tmp_path):
    fields = {"artist": "Țară", "title": "Titlu", "album": "A", "genre": "Techno",
              "year": "1999", "track": "7", "bpm": "130", "key": "Am", "comment": "c"}
    specs = {
        "x.mp3": ["-c:a", "libmp3lame"], "x.flac": ["-c:a", "flac"],
        "x.wav": ["-c:a", "pcm_s16le"], "x.aiff": ["-c:a", "pcm_s16be"],
        "x.m4a": ["-c:a", "aac"], "x.ogg": ["-c:a", "libvorbis"],
    }
    for name, args in specs.items():
        p = make(tmp_path / name, args)
        tagmod.write_tags(p, fields, COVER if not name.endswith(".ogg") else None)
        data = tagmod.read_tags(p, want_cover=True)
        for k in ("artist", "title", "genre", "year", "bpm", "key"):
            assert data.fields[k] == fields[k], (name, k, data.fields[k])
        if not name.endswith(".ogg"):
            assert data.cover and data.cover[0] == COVER[0], name
        assert data.info.duration > 1.5


def test_convert_keeps_tags_and_cover(flac, tmp_path):
    for key, ext, codec in (("mp3_320", ".mp3", None), ("wav16", ".wav", "pcm_s16le"),
                            ("aiff24", ".aiff", "pcm_s24be")):
        jobs = conv.plan_jobs([flac], key, str(tmp_path / "out"), os.path.dirname(flac))
        res = conv.convert_many(jobs, key, sample_rate=44100)
        assert res[0].ok and res[0].message == "Gata", res[0].message
        out = res[0].dest
        assert out.endswith(ext) and os.path.exists(out)
        assert not any(".mmpart" in f for f in os.listdir(os.path.dirname(out)))
        data = tagmod.read_tags(out, want_cover=True)
        assert data.fields["artist"] == "Ana Ș"
        assert data.fields["year"] == "2021"
        assert data.fields["bpm"] == "124"
        assert data.fields["track"] == "3"
        assert data.cover and data.cover[0] == COVER[0]
        assert data.info.sample_rate == 44100
        if key == "mp3_320":
            assert data.info.bitrate >= 300
        if key == "wav16":
            assert data.info.bits_per_sample == 16
    # a doua oară: există deja -> sărit
    jobs = conv.plan_jobs([flac], "mp3_320", str(tmp_path / "out"))
    assert conv.convert_many(jobs, "mp3_320")[0].skipped


def test_scan_with_cache_and_duplicates(flac, tmp_path):
    conv.convert_many(conv.plan_jobs([flac], "mp3_320", None), "mp3_320")
    cache_path = str(tmp_path / "cache.json")
    tracks = scan([os.path.dirname(flac)], LibraryCache(cache_path))
    assert len(tracks) == 2
    assert {t.ext for t in tracks} == {"flac", "mp3"}
    groups = find_duplicates(tracks)
    assert len(groups) == 1 and groups[0][0].ext == "flac"
    # a doua scanare vine din cache
    tracks2 = scan([os.path.dirname(flac)], LibraryCache(cache_path))
    assert [t.path for t in tracks2] == [t.path for t in tracks]


def test_broken_file_does_not_crash(tmp_path):
    p = tmp_path / "broken.mp3"
    p.write_bytes(b"not audio at all" * 10)
    tracks = scan([str(tmp_path)])
    assert len(tracks) == 1
