from music_manager.core.youtube import clean_channel, parse_playlist_id, parse_title


def test_parse_title():
    assert parse_title("Daft Punk - One More Time (Official Video)") == ("Daft Punk", "One More Time")
    assert parse_title("FISHER – Losing It (Kolsch Bootleg) [FREE DL]") == ("FISHER", "Losing It (Kolsch Bootleg)")
    assert parse_title("Glue", "Bicep - Topic") == ("Bicep", "Glue")
    assert parse_title("Track Name [4K] | FREE DOWNLOAD", "Someone") == ("Someone", "Track Name")
    assert parse_title('Moderat "A New Error" (Official Audio)') == ("Moderat", "A New Error")
    assert parse_title("Peggy Gou - (It Goes Like) Nanana") == ("Peggy Gou", "(It Goes Like) Nanana")


def test_ids():
    assert parse_playlist_id("https://www.youtube.com/playlist?list=PLabcdefghij123") == "PLabcdefghij123"
    assert parse_playlist_id("https://music.youtube.com/watch?v=x&list=OLAK5uy_abcdefg") == "OLAK5uy_abcdefg"
    assert parse_playlist_id("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M") is None
    assert clean_channel("Artist - Topic") == "Artist"
