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
    dt = win.download
    st = [SpotifyTrack(str(i), t.title, [t.artist], duration=t.duration) for i, t in enumerate(tracks)]
    dt.current = dt.store.add(__import__("music_manager.core.sources", fromlist=["SavedPlaylist"])
                              .SavedPlaylist(id="p1", name="Test", url=""))
    dt.rows = [Row(st[0], HAVE, tracks[0].path), Row(st[1], MISSING, "", "bicep", "glue"),
               Row(st[2], MISSING, "", "daft punk", "one more time")]
    dt.fill_table(check_missing=True)
    assert dt._checked_rows() == [1, 2]
    assert "(2)" in dt.b_download.text()
    dt.table.selectRow(1)
    dt._ignore_selected(True)
    assert dt.rows[1].status == "ignored" and "1" in dt.current.ignored
    dt.table.item(2, 5).setText("Daft Punk")   # căutare editată
    assert dt.current.custom_queries["2"][0] == "Daft Punk"
    dt.refresh_buttons()
    assert MISSING in DOWNLOADABLE

    win.close()
