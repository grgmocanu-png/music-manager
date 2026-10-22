import os

from music_manager.core.djcollections import (
    best_values, find_traktor_collection, parse_rekordbox_xml, parse_traktor, parse_virtualdj,
)

NML = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="19"><COLLECTION ENTRIES="2">
<ENTRY TITLE="Glue" ARTIST="Bicep"><LOCATION DIR="/:Users/:george/:Music/:" FILE="Bicep - Glue.mp3" VOLUME="C:" VOLUMEID="x"></LOCATION>
<INFO BITRATE="320000" GENRE="Breaks" RANKING="204" COLOR="2"></INFO><TEMPO BPM="129.999954" BPM_QUALITY="100.000000"></TEMPO>
<MUSICAL_KEY VALUE="21"></MUSICAL_KEY></ENTRY>
<ENTRY TITLE="X"><LOCATION DIR="/:Music/:" FILE="x.mp3" VOLUME="D:"></LOCATION><INFO KEY="8A"></INFO></ENTRY>
</COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES><NODE TYPE="PLAYLIST" NAME="Warmup"><PLAYLIST ENTRIES="1" TYPE="LIST">
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:Users/:george/:Music/:Bicep - Glue.mp3"></PRIMARYKEY></ENTRY></PLAYLIST></NODE></SUBNODES></NODE></PLAYLISTS></NML>"""

RB = """<?xml version="1.0" encoding="UTF-8"?><DJ_PLAYLISTS Version="1.0.0"><COLLECTION Entries="1">
<TRACK TrackID="1" Name="Glue" Artist="Bicep" AverageBpm="130.00" Tonality="Am" Rating="255"
 Location="file://localhost/C:/Users/george/Music/Bicep%20-%20Glue.mp3"/></COLLECTION>
<PLAYLISTS><NODE Type="0" Name="ROOT"><NODE Name="Set" Type="1" KeyType="0" Entries="1"><TRACK Key="1"/></NODE></NODE></PLAYLISTS>
</DJ_PLAYLISTS>"""

VDJ = """<?xml version="1.0" encoding="UTF-8"?><VirtualDJ_Database Version="8">
<Song FilePath="C:\\Users\\george\\Music\\Other.mp3"><Tags Genre="House" Stars="3"/><Scan Bpm="0.468750" Key="Fm"/></Song>
</VirtualDJ_Database>"""


def test_parsers(tmp_path):
    docs = tmp_path / "Documents"
    (docs / "Native Instruments" / "Traktor 3.11.1").mkdir(parents=True)
    nml = docs / "Native Instruments" / "Traktor 3.11.1" / "collection.nml"
    nml.write_text(NML, encoding="utf-8")
    assert find_traktor_collection([str(docs)]) == str(nml)
    tk = parse_traktor(str(nml))
    assert not tk.error and len(tk.tracks) == 2
    glue = os.path.normcase(os.path.normpath("C:/Users/george/Music/Bicep - Glue.mp3"))
    info = tk.tracks[glue]
    assert info.key == "Am" and info.rating == 4 and abs(info.bpm - 130) < 0.01
    assert tk.playlists["Warmup"] == [glue]

    rb_path = tmp_path / "rb.xml"
    rb_path.write_text(RB, encoding="utf-8")
    rb = parse_rekordbox_xml(str(rb_path))
    assert rb.tracks[glue].rating == 5 and rb.playlists["Set"] == [glue]

    vp = tmp_path / "database.xml"
    vp.write_text(VDJ, encoding="utf-8")
    vdj = parse_virtualdj(str(vp))
    assert list(vdj.tracks.values())[0].bpm == 128.0

    best, src = best_values("C:/Users/george/Music/Bicep - Glue.mp3", [rb, tk], ["Traktor", "Rekordbox"])
    assert src["bpm"] == "Traktor" and best.rating == 4
