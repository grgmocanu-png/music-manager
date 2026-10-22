"""Test de fum pentru interfață: pornește fereastra și apasă prin taburi (fără ecran)."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QStandardPaths, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from music_manager.core.library import Track  # noqa: E402
from music_manager.core.spotify import SpotifyTrack  # noqa: E402


@pytest.fixture(scope="module")
def app():
    QStandardPaths.setTestModeEnabled(True)
    QCoreApplication.setOrganizationName("MusicManagerTest")
    QCoreApplication.setApplicationName("Music Manager Test")
    a = QApplication.instance() or QApplication([])
    yield a


def _tracks(tmp_path):
    out = []
    for i, (a, t, bpm, key, genre) in enumerate([
        ("Bicep", "Glue", "130", "Am", "Electronic"),
        ("Bicep", "Glue", "130", "Am", "Electronic"),
        ("Daft Punk", "One More Time", "123", "C", "House"),
        ("Moderat", "A New Error", "120", "Em", "Techno"),
    ]):
        p = tmp_path / f"{i}.mp3"
        p.write_bytes(b"x")
        out.append(Track(path=str(p), artist=a, title=t, bpm=bpm, key=key, genre=genre,
                         duration=200 + i, bitrate=320 if i else 128))
    return out


def test_main_window_smoke(app, tmp_path):
    from music_manager.ui.cleanup_tab import CleanupTab  # noqa: F401
    from music_manager.ui.download_tab import DOWNLOADABLE, HAVE, MISSING, Row
    from music_manager.ui.library_tab import DuplicatesDialog, RenameDialog
    from music_manager.ui.main_window import MainWindow
    from music_manager.ui.playlist_tab import harmonic_order
    from music_manager.core.library import find_duplicates

    win = MainWindow()
    win.show()
    app.processEvents()
    tracks = _tracks(tmp_path)

    # bibliotecă + playlisturi
    win.state.set_tracks(tracks)
    win.library.table.set_tracks(tracks)
    win.library.search.setText("bicep")
    assert win.library.table.proxy.rowCount() == 2
    win.playlists.f_bpm_min.setValue(125)
    win.playlists.f_bpm_max.setValue(135)
    assert win.playlists.results.proxy.rowCount() == 2
    win.playlists.add_tracks(tracks)
    assert win.playlists.list.count() == 4
    win.playlists.sort_bpm()
    win.playlists.move_selected(1)
    assert len(harmonic_order(tracks)) == 4
    win.state.send_to_playlist.emit(tracks[:1])
    assert win.sections.currentWidget() is win.lib

    # conversie
    win.state.send_to_convert.emit([t.path for t in tracks])
    assert len(win.convert.files) == 4
    assert win.sections.currentWidget() is win.files and win.files.currentWidget() is win.convert
    win.convert.preset.setCurrentIndex(2)   # WAV -> avertisment pentru MP3-uri
    assert "comprimate" in win.convert.warning.text()

    # curățenie
    win.cleanup._scanned(tracks)
    dlg = DuplicatesDialog(find_duplicates(tracks), win)
    assert dlg.tree.topLevelItemCount() == 1
    rd = RenameDialog(tracks, win)
    assert rd.table.rowCount() == 4

    # download: rânduri simulate
    from music_manager.core.sources import SavedPlaylist
    dt = win.download
    pp = dt.playlists
    st = [SpotifyTrack(str(i), t.title, [t.artist], duration=t.duration) for i, t in enumerate(tracks)]
    pp.current = dt.store.add(SavedPlaylist(id="p1", name="Test", url=""))
    pp.rows = [Row(st[0], HAVE, tracks[0].path), Row(st[1], MISSING, "", "bicep", "glue"),
               Row(st[2], MISSING, "", "daft punk", "one more time"), Row(st[3], "deleted", "", "m", "a")]
    pp.fill_table(auto_check=True)
    assert pp._checked_rows() == [1, 2]                 # cea ștearsă nu se bifează
    assert "(2)" in pp.b_download.text()
    pp.table.selectRow(1)
    pp._ignore_selected(True)
    assert pp.rows[1].status == "ignored" and "1" in pp.current.ignored
    pp.table.item(2, 5).setText("Daft Punk")            # căutare editată
    assert pp.current.custom_queries["2"][0] == "Daft Punk"
    pp.refresh_buttons()
    assert MISSING in DOWNLOADABLE

    # negăsite
    from music_manager.core.sources import track_to_dict
    pp.current.not_found["2"] = {"reason": "negăsită pe Soulseek", "code": "NoResults", "date": "2026-10-22T17:00",
                                 "track": track_to_dict(st[2])}
    pp.current.not_found["9"] = "format vechi"
    dt.store.save()
    dt.notfound.reload()
    assert len(dt.notfound.rows) == 1 or len(dt.notfound.rows) == 2
    assert "Negăsite" in dt.inner.tabText(1)
    dt.notfound.table.selectRow(0)
    dt.notfound._remove_selected(ignore=False)
    assert "2" not in pp.current.not_found and "2" in pp.current.seen

    # oprire: butoanele apar doar în timpul descărcării
    dt.set_busy("x", stoppable=True)
    assert dt.b_stop_soft.isVisibleTo(dt) and dt.b_stop_hard.isVisibleTo(dt)
    dt.set_idle()
    assert not dt.b_stop_soft.isVisibleTo(dt)

    # căutare manuală pe Soulseek (fără rețea: rezultate simulate)
    from music_manager.core.soulseek import SearchResult
    from music_manager.ui.slsk_search import ManualSearchDialog
    d = ManualSearchDialog("bicep glue", "Bicep - Glue", 269, win)
    d._done([SearchResult("a", "@@x\\Glue.mp3", 9_000_000, 320, 270, speed=2.0, free_slot=True),
             SearchResult("b", "@@x\\Glue (live).mp3", 9_000_000, 320, 400),
             SearchResult("c", "@@x\\Glue.mp3", 3_000_000, 128, 269)])
    assert d.table.rowCount() == 1            # 128 kbps și durata greșită sunt ascunse
    d.only_good.setChecked(False)
    d.only_len.setChecked(False)
    assert d.table.rowCount() == 3
    d.table.selectRow(0)
    d.take()
    assert d.chosen is not None

    win.close()
