import os

from music_manager.core.djcollections import Collection, DJInfo, norm_path, parse_rekordbox_xml, parse_traktor
from music_manager.core.libdb import (
    LibraryDB, artist_groups, build_items, export_rekordbox_xml, export_traktor_nml, genre_counts,
    mixable, smart_filter,
)
from music_manager.core.library import Track


def _tracks(tmp_path):
    specs = [("Bicep", "Glue", "Breaks", "130", "Am", "2017"),
             ("bicep", "Apricots", "House", "", "", "2021"),
             ("Moderat", "Bad Kingdom", "Techno", "124", "Em", "2013"),
             ("The Prodigy", "Breathe", "Breaks", "130", "C", "1996")]
    out = []
    for i, (a, t, g, b, k, y) in enumerate(specs):
        p = tmp_path / f"{i}.mp3"
        p.write_bytes(b"x")
        out.append(Track(path=str(p), artist=a, title=t, genre=g, bpm=b, key=k, year=y, duration=300))
    return out


def test_priority_and_rules(tmp_path):
    tracks = _tracks(tmp_path)
    db = LibraryDB(str(tmp_path / "db.json"))
    db.extra(tracks[1].path).an_bpm = 122.0
    db.extra(tracks[1].path).an_key = "Fm"
    tk = Collection("Traktor", "x")
    tk.tracks[norm_path(tracks[0].path)] = DJInfo(bpm=129.98, key="Am", rating=4)
    items = build_items(tracks, db, [tk])
    assert items[0].bpm == 129.98 and items[0].bpm_src == "Traktor" and items[0].rating == 4
    assert items[0].in_traktor and not items[2].in_traktor
    assert items[1].bpm == 122.0 and items[1].key_src == "Analiză"
    assert items[2].bpm_src == "Fișier"

    r = {"match": "all", "rules": [{"field": "genre", "op": "este oricare din", "value": "Breaks, House"},
                                    {"field": "bpm", "op": "între", "value": [120, 125]}]}
    assert [i.track.title for i in smart_filter(items, r)] == ["Apricots"]
    r = {"match": "any", "rules": [{"field": "key", "op": "compatibilă cu", "value": "8A"}]}
    assert {i.track.title for i in smart_filter(items, r)} == {"Glue", "Bad Kingdom", "Breathe"}
    r = {"rules": [{"field": "in_traktor", "op": "nu", "value": ""}]}
    assert len(smart_filter(items, r)) == 3

    mix = mixable(items[0], items)
    assert [m.track.title for m in mix] == ["Breathe", "Bad Kingdom"]

    assert genre_counts(items)[0] == ("Breaks", 2)
    assert artist_groups(items) == [[("Bicep", 1), ("bicep", 1)]] or len(artist_groups(items)) == 1

    pl = db.new_playlist("Set")
    pl.tracks = [tracks[0].path]
    db.save()
    db2 = LibraryDB(str(tmp_path / "db.json"))
    assert db2.get_playlist(pl.id).tracks == [tracks[0].path]
    assert db2.extras[norm_path(tracks[1].path)].an_bpm == 122.0


def test_exports_roundtrip(tmp_path):
    tracks = _tracks(tmp_path)
    db = LibraryDB(str(tmp_path / "db.json"))
    items = build_items(tracks, db, [])
    x = tmp_path / "rb.xml"
    export_rekordbox_xml(str(x), [("Set A", items[:2]), ("Set B", items[1:3])])
    rb = parse_rekordbox_xml(str(x))
    assert len(rb.tracks) == 3 and len(rb.playlists["Set B"]) == 2
    assert rb.tracks[norm_path(tracks[0].path)].bpm == 130
    n = tmp_path / "set.nml"
    export_traktor_nml(str(n), "Set", items[:3])
    tk = parse_traktor(str(n))
    assert len(tk.tracks) == 3 and len(tk.playlists["Set"]) == 3
    assert tk.tracks[norm_path(tracks[0].path)].key == "Am"


def test_comment_roles_energy(tmp_path):
    from music_manager.core.libdb import compose_comment, parse_comment, suggest_role
    assert parse_comment("Deep House | Peak | E7") == ("Deep House", "Peak", 7)
    assert parse_comment("Deep House, Minimal") == ("Deep House, Minimal", "", 0)
    assert parse_comment("warm up | energy 4") == ("", "Warm-up", 4)
    assert compose_comment("Deep House", "Peak", 7) == "Deep House | Peak | E7"
    assert compose_comment("", "Closer") == "Closer"
    assert suggest_role(8, 128) == "Peak"
    assert suggest_role(2, 120) == "Opener"
    assert suggest_role(3, 95) == "After"
    assert suggest_role(8, 128, plays=3, avg_pos=0.95) == "Closer"

    tracks = _tracks(tmp_path)
    tracks[0].comment = "Breaks | Closer | E9"
    db = LibraryDB(str(tmp_path / "db.json"))
    db.extra(tracks[2].path).an_energy = 3
    db.extra(tracks[3].path).role = "Opener"
    items = build_items(tracks, db, [])
    assert items[0].role == "Closer" and items[0].energy == 9 and items[0].style == "Breaks"
    assert items[2].role == "" and items[2].role_suggested == "Opener"
    r = {"rules": [{"field": "role", "op": "este oricare din", "value": "Opener"}]}
    assert [i.track.title for i in smart_filter(items, r)] == ["Breathe"]
    r = {"rules": [{"field": "role", "op": "este oricare din (inclusiv sugestii)", "value": "Opener"}]}
    assert {i.track.title for i in smart_filter(items, r)} == {"Breathe", "Bad Kingdom"}
    r = {"rules": [{"field": "energy", "op": "între", "value": [8, 10]}]}
    assert [i.track.title for i in smart_filter(items, r)] == ["Glue"]


def test_traktor_history(tmp_path):
    from music_manager.core.libdb import parse_traktor_history
    hist = tmp_path / "History"
    hist.mkdir()
    entries = "".join(f'<ENTRY><LOCATION DIR="/:m/:" FILE="t{i}.mp3" VOLUME="C:"></LOCATION></ENTRY>' for i in range(10))
    keys = "".join(f'<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:m/:t{i}.mp3"></PRIMARYKEY></ENTRY>' for i in range(10))
    nml = (f'<NML VERSION="19"><COLLECTION>{entries}</COLLECTION><PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT">'
           f'<SUBNODES><NODE TYPE="PLAYLIST" NAME="HISTORY"><PLAYLIST TYPE="LIST">{keys}</PLAYLIST></NODE>'
           f'</SUBNODES></NODE></PLAYLISTS></NML>')
    (hist / "history_2026_10_01.nml").write_text(nml, encoding="utf-8")
    h = parse_traktor_history(str(hist))
    assert len(h) == 10
    last = [v for k, v in h.items() if k.endswith("t9.mp3")][0]
    assert last == [1.0]
