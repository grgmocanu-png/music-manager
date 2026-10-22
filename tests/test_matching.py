from music_manager.core.library import Track
from music_manager.core.matching import Matcher, search_query, split_title
from music_manager.core.spotify import SpotifyTrack, parse_playlist_id, track_from_json


def S(title, *artists, dur=300):
    return SpotifyTrack(id="x", title=title, artists=list(artists), duration=dur)


def L(path, artist="", title="", dur=300):
    return Track(path=path, artist=artist, title=title, duration=dur)


def test_split_title():
    assert split_title("Strobe - Radio Edit") == ("strobe", "")
    assert split_title("Blue Monday - 1988 Remaster") == ("blue monday", "")
    assert split_title("Lose Yourself (Kölsch Remix)") == ("lose yourself", "kolsch remix")
    assert split_title("Lose Yourself - Kölsch Remix") == ("lose yourself", "kolsch remix")
    assert split_title("Song (feat. Someone) [Extended Mix]") == ("song", "extended mix")
    assert split_title("Track (Official Video) www.site.com") == ("track", "")


def test_matching():
    lib = [
        L("/m/New Order - Blue Monday.mp3", dur=448),
        L("/m/a.flac", "deadmau5", "Strobe (Radio Edit)", 210),
        L("/m/b.mp3", "Mehro", "Lose Yourself (Original Mix)", 400),
        L("/m/01 - Tame Impala - Let It Happen [320kbps].mp3", dur=467),
        L("/m/c.mp3", "Bicep", "Glue", 269),
    ]
    m = Matcher(lib)
    assert m.find(S("Blue Monday - 1988 Remaster", "New Order", dur=449)).path == "/m/New Order - Blue Monday.mp3"
    assert m.find(S("Strobe - Radio Edit", "deadmau5", dur=211)).path == "/m/a.flac"
    assert m.find(S("Lose Yourself - Kölsch Remix", "Mehro", "Kölsch", dur=410)) is None
    assert m.find(S("Lose Yourself", "Mehro", dur=401)).path == "/m/b.mp3"
    assert m.find(S("Let It Happen", "Tame Impala", dur=467)).path.endswith("[320kbps].mp3")
    assert m.find(S("Glue", "Someone Else", dur=269)) is None            # alt artist
    assert m.find(S("Glue", "Bicep", dur=420)) is None                   # durată foarte diferită


def test_search_query():
    assert search_query(S("Strobe - Radio Edit", "deadmau5")) == ("deadmau5", "strobe")
    assert search_query(S("L'été (feat. X) - Kölsch Remix", "Røyksopp", "X")) == ("royksopp", "l ete kolsch remix")


def test_parse_playlist_id():
    pid = "37i9dQZF1DXcBWIGoYBM5M"
    assert parse_playlist_id(f"https://open.spotify.com/playlist/{pid}?si=abc") == pid
    assert parse_playlist_id(f"spotify:playlist:{pid}") == pid
    assert parse_playlist_id(pid) == pid
    assert parse_playlist_id("https://open.spotify.com/album/xyz") is None


def test_track_from_json_new_and_old_shape():
    t = {"type": "track", "id": "1", "name": "Song", "duration_ms": 200000,
         "artists": [{"name": "A"}, {"name": "B"}], "album": {"name": "Al", "release_date": "2020-01-02"},
         "external_ids": {"isrc": "X"}}
    for e in ({"item": t, "added_at": "2024"}, {"track": t}):
        s = track_from_json(e)
        assert s.title == "Song" and s.artist == "A, B" and s.year == "2020" and s.duration == 200
    assert track_from_json({"item": {**t, "is_local": True}}) is None
    assert track_from_json({"item": {"type": "episode", "id": "e", "name": "Pod"}}) is None
    assert track_from_json({"item": None}) is None


def test_classify_rules():
    from music_manager.core.sources import SavedPlaylist, classify
    p = SavedPlaylist(id="p", name="P", url="", baseline="2026-06-01T12:00",
                      seen=["deleted"], ignored=["ign"], not_found={"nf": "x"})
    def t(i, added):
        return SpotifyTrack(id=i, title="T", artists=["A"], added_at=added)
    assert classify(p, t("x", "2026-07-01T10:00:00Z"), True) == "have"
    assert classify(p, t("ign", "2026-07-01T10:00:00Z"), False) == "ignored"
    assert classify(p, t("nf", "2026-07-01T10:00:00Z"), False) == "notfound"
    assert p.not_found["nf"]["reason"] == "x"          # format vechi convertit
    assert classify(p, t("deleted", "2026-07-01T10:00:00Z"), False) == "deleted"
    assert classify(p, t("old", "2025-01-01T10:00:00Z"), False) == "old"
    assert classify(p, t("new", "2026-09-01T10:00:00Z"), False) == "missing"
    p.baseline = ""
    assert classify(p, t("old", "2025-01-01T10:00:00Z"), False) == "missing"


def test_wide_query():
    from music_manager.core.matching import wide_query
    assert wide_query(S("Somebody That I Used To Know", "Gotye")) == ("gotye", "somebody")
    assert wide_query(S("Lose Yourself - Kölsch Remix", "Mehro")) == ("mehro", "yourself kolsch")
    assert wide_query(S("I Love You", "X")) == ("x", "i love you")      # doar cuvinte comune
