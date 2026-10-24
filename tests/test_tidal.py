from music_manager.core import tidal as td


def test_parse():
    u = "https://tidal.com/browse/playlist/0B3E2A6F-8B2C-4E5D-9F10-1234567890AB?u"
    assert td.parse_playlist_id(u) == "0b3e2a6f-8b2c-4e5d-9f10-1234567890ab"
    assert td.parse_playlist_id("https://listen.tidal.com/playlist/0b3e2a6f-8b2c-4e5d-9f10-1234567890ab")
    assert td.parse_playlist_id("https://open.spotify.com/playlist/abc") is None
    assert td.is_tidal(u) and not td.is_tidal("https://youtube.com/x")
    assert td.parse_duration("PT3M33S") == 213 and td.parse_duration("PT1H2M") == 3720


def test_get_playlist_pages_and_artists():
    c = td.TidalClient("id", "secret")
    calls = []

    def fake(path, params=None, user=None, _retry=0):
        calls.append((path, params))
        if path == "/playlists/p1":
            return {"data": {"attributes": {"name": "Deep"}}}
        if path.startswith("/playlists/p1/relationships/items") and params:
            return {"data": [{"id": "1", "type": "tracks", "meta": {"addedAt": "2026-01-02T00:00:00Z"}},
                             {"id": "v", "type": "videos"}],
                    "included": [{"id": "1", "type": "tracks",
                                  "attributes": {"title": "Glue", "duration": "PT4M29S", "isrc": "GB1"}}],
                    "links": {"next": "/playlists/p1/relationships/items?page[cursor]=x"}}
        if "page[cursor]=x" in path:
            return {"data": [{"id": "2", "type": "tracks"}],
                    "included": [{"id": "2", "type": "tracks",
                                  "attributes": {"title": "Atlas", "version": "Extended", "duration": "PT6M"}}],
                    "links": {}}
        if path == "/tracks":
            return {"data": [{"id": "1", "relationships": {"artists": {"data": [{"id": "a"}]}}},
                             {"id": "2", "relationships": {"artists": {"data": [{"id": "a"}, {"id": "b"}]}}}],
                    "included": [{"id": "a", "type": "artists", "attributes": {"name": "Bicep"}},
                                 {"id": "b", "type": "artists", "attributes": {"name": "Hammer"}}]}
        raise AssertionError(path)
    c.get = fake
    pl = c.get_playlist("p1")
    assert pl.name == "Deep" and pl.skipped == 1 and len(pl.tracks) == 2
    a, b = pl.tracks
    assert a.id == "tidal:1" and a.artists == ["Bicep"] and a.duration == 269 and a.isrc == "GB1"
    assert a.added_at.startswith("2026-01-02")
    assert b.title == "Atlas (Extended)" and b.artists == ["Bicep", "Hammer"] and b.duration == 360
