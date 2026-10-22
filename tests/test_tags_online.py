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
