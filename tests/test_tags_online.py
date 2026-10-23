from music_manager.core.genres import GenreMapper
from music_manager.core.tagsearch import TagFinder


def test_genre_mapper():
    m = GenreMapper()
    assert m.family_of("Deep House") == "House"
    assert m.family_of("Melodic Deep House") == "House"
    assert m.family_of("Italo-Disco") == "Disco"
    assert m.classify(["Minimal", "Tech House"]) == "Techno"
    assert m.classify([], ["Funk / Soul"]) == "Funk & Soul"
    assert m.unknown_styles(["Deep House", "Zzz Weird"]) == ["Zzz Weird"]


def test_discogs_parsing(monkeypatch):
    f = TagFinder(discogs_token="t", use_musicbrainz=False)
    calls = []

    def fake(url, headers=None, retries=3):
        calls.append(url)
        return {"results": [
            {"title": "Other Guy - Thing", "year": "1990", "style": ["Trance"], "genre": ["Electronic"]},
            {"title": "Bicep - Isles", "year": "2021", "style": ["Breakbeat", "Deep House"],
             "genre": ["Electronic"], "label": ["Ninja Tune"], "cover_image": "https://img/x.jpg"},
            {"title": "Bicep - Glue", "year": "2017", "style": ["Deep House", "Breakbeat"],
             "genre": ["Electronic"], "label": ["Ninja Tune (2)"], "cover_image": "https://img/y.jpg"},
        ]}
    monkeypatch.setattr(f, "_get_json", fake)
    monkeypatch.setattr(f._discogs_t, "wait", lambda: None)
    r = f.find("Bicep", "Glue", 269)
    assert r.year == "2017"                 # prima lansare
    assert r.label == "Ninja Tune"
    assert r.cover_url == "https://img/y.jpg"
    assert r.genre in ("House", "Breaks")
    assert "Deep House" in r.styles and "Trance" not in r.styles   # alt artist ignorat
    assert r.sources["year"] == "Discogs"


def test_title_and_artist_variants():
    from music_manager.core.tagsearch import artist_variants, rare_word, title_variants
    assert artist_variants("Marlon Hoffstadt aka DJ Daddy Trance")[1] == "Marlon Hoffstadt"
    tv = title_variants("Music a Fe Rule, Part 1 (Tornado Wallace Remix)")
    assert tv[0].startswith("Music a Fe Rule, Part 1 (")
    assert "Music a Fe Rule" in tv
    assert rare_word("Tastes Like Freedom (Red Face Remix)") == "freedom"


def test_file_genre_and_bandcamp():
    from music_manager.core.tagsearch import file_genre, parse_bandcamp
    m = GenreMapper()
    r = file_genre({"mood": "techno x trance"}, m)
    assert r.genre == "Techno" and r.only_genre and "Mood" in r.source
    assert file_genre({"mood": "happy"}, m) is None
    html = ('<a class="tag" href="x">techno</a><a class="tag" href="y">berlin</a>'
            '<script type="application/ld+json">{"name": "It", "datePublished": "03 Mar 2023 00:00:00 GMT",'
            '"publisher": {"name": "00 Energy"}, "byArtist": {"name": "Marlon"},'
            '"image": "https://f4.bcbits.com/img/a.jpg"}</script>')
    b = parse_bandcamp(html, m, "Marlon")
    assert (b.genre, b.year, b.label) == ("Techno", "2023", "00 Energy")


def test_not_found_reasons_and_artist_fallback():
    f = TagFinder(discogs_token="t", use_musicbrainz=False)
    f._discogs_t.wait = lambda: None

    def fake(url, headers=None, retries=3):
        if "track=" in url or "q=" in url:
            return {"results": []}
        return {"results": [{"title": "Bicep - A", "style": ["Deep House"], "genre": ["Electronic"]},
                            {"title": "Bicep - B", "style": ["Deep House", "Breakbeat"], "genre": ["Electronic"]}]}
    f._get_json = fake
    r = f.find("Bicep", "Unknown Song", 200)
    assert r.genre == "House" and r.sources["genre"] == "Discogs (după artist)"
    assert not r.year                            # după artist: doar genul
    assert any(x.startswith("Discogs:") for x in r.reasons)
