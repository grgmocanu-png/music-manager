from music_manager.core.library import Track
from music_manager.core.namecleanup import clean_tag_fields, clean_text, plan_name_cleanup


def test_clean_text():
    assert clean_text("01_artist_-_track_(www.mp3site.ru)_[320kbps]") == "artist - track"
    assert clean_text("Track Name (Official Video) [FREE DOWNLOAD]") == "Track Name"
    assert clean_text("A1. Artist – Song") == "Artist - Song"
    assert clean_text("Bicep - Glue (1)") == "Bicep - Glue"
    assert clean_text("Daft Punk - One More Time (Radio Edit)") == "Daft Punk - One More Time (Radio Edit)"
    assert clean_text("Song ytmp3") == "Song"
    assert clean_text("808 State - Pacific 707") == "808 State - Pacific 707"   # cifrele din nume rămân
    assert clean_text("Jay-Z - Hip-Hop") == "Jay-Z - Hip-Hop"
    assert clean_text("50 Cent - In Da Club") == "50 Cent - In Da Club"


def test_plan(tmp_path):
    a = tmp_path / "01 - Bicep - Glue [320kbps].mp3"
    b = tmp_path / "Bicep - Glue.mp3"
    a.write_bytes(b"1")
    c = tmp_path / "Moderat - Bad Kingdom (Official Video).MP3"
    c.write_bytes(b"2")
    plan = plan_name_cleanup([Track(path=str(a)), Track(path=str(c))])
    assert [p.status for p in plan] == ["ok", "ok"]
    assert plan[0].dest.endswith("Bicep - Glue.mp3")
    assert plan[1].dest.endswith("Moderat - Bad Kingdom.mp3")
    b.write_bytes(b"x")
    assert plan_name_cleanup([Track(path=str(a))])[0].status == "conflict"


def test_tag_fields():
    t = Track(path="/x.mp3", artist="Bicep", title="Glue (Official Audio)", album="www.site.com")
    assert clean_tag_fields(t) == {"title": "Glue"}
