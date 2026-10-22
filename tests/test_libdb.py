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
