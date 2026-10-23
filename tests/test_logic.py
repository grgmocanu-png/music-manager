"""Teste pentru logica care nu depinde de fișiere audio reale."""
import os

from music_manager.core.library import (
    Track, find_duplicates, normalize, plan_renames, render_template,
    apply_renames, undo_last, RENAME_PRESETS,
)
from music_manager.core.playlist import (
    Filter, apply_filter, compatible_keys, read_m3u, to_camelot, write_m3u,
)


def T(**kw):
    kw.setdefault("path", "/x/" + kw.get("title", "a") + ".mp3")
    return Track(**kw)


def test_normalize():
    assert normalize("Beyoncé feat. Jay-Z") == "beyonce"
    assert normalize("Song (Original Mix)") == "song"
    assert normalize("The Prodigy") == normalize("Prodigy")
    assert normalize("Simon & Garfunkel") == "simon and garfunkel"


def test_duplicates_prefers_quality_and_respects_duration():
    a = T(path="/a/1.mp3", artist="Daft Punk", title="One More Time", duration=320, bitrate=128)
    b = T(path="/a/2.flac", artist="daft punk", title="One More Time (Radio Edit)", duration=322,
          lossless=True, bitrate=900, bits_per_sample=16, sample_rate=44100)
    c = T(path="/a/3.mp3", artist="Daft Punk", title="One More Time", duration=600, bitrate=320)  # versiune lungă
    d = T(path="/a/Daft Punk - One More Time.mp3", duration=321, bitrate=320)  # fără taguri
    groups = find_duplicates([a, b, c, d])
    assert len(groups) == 1
    g = groups[0]
    assert g[0] is b                      # FLAC primul
    assert {x.path for x in g} == {a.path, b.path, d.path}


def test_render_template():
    t = T(artist="AC/DC", title="Back In Black", track="1", bpm="94", key="")
    assert render_template("{artist} - {title}", t) == "AC-DC - Back In Black"
    assert render_template("{track:02} {artist} - {title}", t) == "01 AC-DC - Back In Black"
    assert render_template("{artist} - {title} [{bpm} {key}]", t) == "AC-DC - Back In Black [94]"
    assert render_template("{artist} - {title}", T(title="X")) is None
    assert render_template("{albumartist}/{album}/{title}", t) == "AC-DC/Necunoscut/Back In Black"
    assert render_template('{artist} - {title}', T(artist='A', title='What? Why: "No"')) == "A - What Why No"


def test_rename_and_undo(tmp_path):
    d = tmp_path / "music"
    d.mkdir()
    (d / "track01.mp3").write_bytes(b"x")
    (d / "track02.mp3").write_bytes(b"y")
    (d / "Existing - Song.mp3").write_bytes(b"z")
    tracks = [
        T(path=str(d / "track01.mp3"), artist="Artist", title="Song"),
        T(path=str(d / "track02.mp3"), artist="Existing", title="Song"),  # conflict cu fișier existent
    ]
    plan = plan_renames(tracks, RENAME_PRESETS["Artist - Titlu"])
    assert [p.status for p in plan] == ["ok", "conflict"]
    logs = str(tmp_path / "logs")
    moves, errors = apply_renames(plan, logs)
    assert len(moves) == 1 and not errors
    assert (d / "Artist - Song.mp3").exists() and not (d / "track01.mp3").exists()
    assert (d / "Existing - Song.mp3").read_bytes() == b"z"   # nimic suprascris
    ok, fail, kind = undo_last(logs)
    assert (ok, fail, kind) == (1, 0, "redenumire")
    assert (d / "track01.mp3").exists()


def test_rename_into_folders(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "a.flac").write_bytes(b"1")
    t = T(path=str(d / "a.flac"), artist="Nina", albumartist="", album="Live", title="Feeling", track="3")
    plan = plan_renames([t], RENAME_PRESETS["Artist/Album/Nr. Titlu (foldere)"], base_dir=str(tmp_path / "out"))
    apply_renames(plan, str(tmp_path / "logs"))
    assert (tmp_path / "out" / "Nina" / "Live" / "03 Feeling.flac").exists()
    assert not d.exists()   # folderul gol a fost curățat


def test_camelot():
    assert to_camelot("Am") == "8A"
    assert to_camelot("A minor") == "8A"
    assert to_camelot("C") == "8B"
    assert to_camelot("Cmaj") == "8B"
    assert to_camelot("Ebm") == "2A"
    assert to_camelot("F#") == "2B"
    assert to_camelot("11a") == "11A"
    assert to_camelot("1m") == "8A"     # Open Key
    assert to_camelot("6m") == "1A"
    assert to_camelot("garbage") == ""
    assert compatible_keys("8A") == {"8A", "9A", "7A", "8B"}
    assert compatible_keys("12B") == {"12B", "1B", "11B", "12A"}


def test_filter():
    tracks = [
        T(title="a", artist="X", genre="House", year="2005", bpm="124", key="Am"),
        T(title="b", artist="Y", genre="Deep House", year="2015", bpm="62", key="Em"),
        T(title="c", artist="Z", genre="Techno", year="2020", bpm="135", key="C"),
    ]
    assert [t.title for t in apply_filter(tracks, Filter(genre="house"))] == ["a", "b"]
    assert [t.title for t in apply_filter(tracks, Filter(bpm_min=120, bpm_max=128))] == ["a", "b"]  # 62*2
    assert [t.title for t in apply_filter(tracks, Filter(year_min=2010))] == ["b", "c"]
    assert [t.title for t in apply_filter(tracks, Filter(key="8A", harmonic=True))] == ["a", "b", "c"]
    assert [t.title for t in apply_filter(tracks, Filter(key="8A"))] == ["a"]
    assert [t.title for t in apply_filter(tracks, Filter(text="z"))] == ["c"]


def test_m3u_roundtrip(tmp_path):
    music = tmp_path / "music" / "sub"
    music.mkdir(parents=True)
    f1 = music / "Ș song.mp3"
    f1.write_bytes(b"x")
    pl = tmp_path / "lists" / "set.m3u8"
    pl.parent.mkdir()
    write_m3u(str(pl), [T(path=str(f1), artist="Ana", title="Ș song", duration=200)])
    text = pl.read_text(encoding="utf-8")
    assert "#EXTINF:200,Ana - Ș song" in text
    assert os.path.normpath(read_m3u(str(pl))[0]) == os.path.normpath(str(f1))


def test_safe_write_and_duplicates_dest(tmp_path):
    import json
    from music_manager.core.library import duplicates_dest
    from music_manager.core.safeio import write_json
    p = tmp_path / "a" / "b.json"
    write_json(str(p), {"x": 1})
    write_json(str(p), {"x": 2})
    assert json.loads(p.read_text())["x"] == 2
    assert not [f for f in os.listdir(p.parent) if f.endswith(".tmp")]
    src = tmp_path / "x.mp3"
    src.write_bytes(b"1")
    taken = set()
    d1 = duplicates_dest(str(src), str(tmp_path / "A - B.mp3"), taken)
    d2 = duplicates_dest(str(src), str(tmp_path / "A - B.mp3"), taken)
    assert os.path.basename(os.path.dirname(d1)) == "_Dubluri"
    assert d1 != d2 and d2.endswith("(2).mp3")
