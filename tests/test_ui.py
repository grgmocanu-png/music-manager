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


def _wait(app, task, timeout=20):
    import time
    end = time.time() + timeout
    while task is not None and task.isRunning() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


def test_main_window_smoke(app, tmp_path):
    from music_manager.ui.cleanup_tab import CleanupTab  # noqa: F401
    from music_manager.ui.download_tab import DOWNLOADABLE, HAVE, MISSING, Row
    from music_manager.ui.library_tab import DuplicatesDialog, RenameDialog
    from music_manager.ui.main_window import MainWindow
    from music_manager.core.library import find_duplicates
    from music_manager.core import libdb as ldb

    win = MainWindow()
    win.show()
    for _ in range(30):
        app.processEvents()
    lv = win.library
    _wait(app, lv.task)
    lv.timer.stop()
    tracks = _tracks(tmp_path)

    # bibliotecă: filtre, playlisturi, mixabile
    lv._tracks = tracks
    lv.collections = []
    lv._rebuild_items()
    lv.rebuild_tree()
    lv.show_view(("all",))
    assert lv.table.proxy.rowCount() == 4
    lv.search.setText("bicep")
    assert lv.table.proxy.rowCount() == 2
    lv.clear_filters()
    lv.bpm_lo.setValue(125)
    lv.bpm_hi.setValue(135)
    assert lv.table.proxy.rowCount() == 2
    lv.clear_filters()
    p = lv.db.new_playlist("Set")
    lv.add_to_playlist(p.id, [t.path for t in tracks[:3]])
    lv.show_view(("pl", p.id))
    assert lv.table.proxy.rowCount() == 3
    lv.move_in_playlist([lv.by_path[ldb.norm_path(tracks[2].path)]], -1)
    assert lv.db.get_playlist(p.id).tracks[1] == tracks[2].path
    sp = lv.db.new_playlist("House", "smart")
    sp.rules = {"match": "all", "rules": [{"field": "genre", "op": "este oricare din", "value": "House"}]}
    lv.show_view(("smart", sp.id))
    assert lv.table.proxy.rowCount() == 1
    lv.show_mixable(lv.items[0])
    assert lv.view == ("mix",)
    lv.set_role(lv.items[:1], "Peak", write=False)
    assert lv.items[0].role == "Peak" and lv.db.extra(lv.items[0].path).role == "Peak"
    lv.role_f.setCurrentIndex(4)          # Peak
    lv.set_rating(lv.items[:1], 4)
    assert lv.db.extra(lv.items[0].path).rating == 4
    # playlist cu piesă dispărută
    p.tracks.append(str(tmp_path / "dispărută.mp3"))
    lv.rebuild_tree()
    assert any(m.missing for m in lv._missing_items())

    # familii, sugestii de playlist, coloana Playlisturi
    daft = next(i for i in lv.items if i.track.artist == "Daft Punk")
    assert daft.family == "House"
    assert lv._suggest_playlists([daft], "bpm")[1] == "120–130"
    assert lv._suggest_playlists([daft], "family")[1] == ""      # există deja „House”
    assert lv._suggest_playlists([daft], "title") == ([], "")
    assert "Set" in daft.playlists
    # v0.9.1: editare în tabel, sugestii pe orice coloană, Cover
    from music_manager.ui.library_view import COLS as LCOLS
    keys = [k for _, k in LCOLS]
    m = lv.table.model_
    lv.show_view(("all",))
    row = m.items.index(daft)
    assert m.setData(m.index(row, keys.index("family")), "Disco")
    assert daft.family == "Disco" and lv.db.extra(daft.path).user_family == "Disco"
    assert m.setData(m.index(row, keys.index("bpm")), "123.5")
    assert daft.bpm == 123.5 and daft.bpm_src == "Manual"
    from PySide6.QtWidgets import QMessageBox
    _info = QMessageBox.information
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)   # fără ferestre în test
    try:
        assert not m.setData(m.index(row, keys.index("bpm")), "abc")
    finally:
        QMessageBox.information = _info
    assert m.setData(m.index(row, keys.index("energy")), "8")
    assert daft.energy == 8
    assert m.setData(m.index(row, keys.index("label")), "Roulé")
    assert daft.track.label == "Roulé"
    assert lv._suggest_playlists([daft], "label")[1] == "Roulé"
    assert m.data(m.index(row, keys.index("cover"))) in ("✓", "")
    # v0.9: Curăță categorii, căutare piesă lipsă, coloane în engleză
    from music_manager.ui.library_dialogs import CategoryCleanupDialog, FindInLibraryDialog
    cd = CategoryCleanupDialog(lv.items, "comment", win)
    assert cd.table.rowCount() >= 1
    cd2 = CategoryCleanupDialog(lv.items, "artist", win)
    assert cd2.table.rowCount() >= 1
    miss = ldb.LibItem(track=Track(path=str(tmp_path / "old" / "08. daft punk - x.mp3")), missing=True)
    fd = FindInLibraryDialog(miss, lv.items, win)
    assert fd.query.text().startswith("daft punk")
    assert [c for c, _ in __import__("music_manager.ui.library_view", fromlist=["COLS"]).COLS][6] == "Comments"
    lv.fam_menu.actions()[2].setChecked(True)
    lv.clear_filters()
    lv.table._reset_columns()

    # activități în fundal: o operație care scrie fișiere, apoi una care așteaptă după ea
    from music_manager.ui.jobs import Job, manager
    order = []
    j1 = Job("test 1", lambda p, c: (order.append(1), 1)[1])
    j2 = Job("test 2", lambda p, c: (order.append(2), 2)[1])
    j1.start()
    j2.start()
    _wait(app, j1)
    _wait(app, j2)
    assert order == [1, 2] and j2.status == "done"

    # player: waveform desenat din analiză simulată
    from music_manager.core.analysis import Analysis
    a = Analysis(bpm=128, key="Am", duration=200, peak=[100] * 50, low=[200] * 50, mid=[80] * 50,
                 high=[40] * 50, drops=[60.0])
    lv.player.wave.set_data(a, 200)
    lv.player.wave.set_position(0.3)
    assert not lv.player.wave.grab().isNull()

    # conversie
    win.state.send_to_convert.emit([t.path for t in tracks])
    assert len(win.convert.files) == 4
    assert win.sections.currentWidget() is win.files and win.files.currentWidget() is win.convert
    win.convert.preset.setCurrentIndex(2)   # WAV -> avertisment pentru MP3-uri
    assert "comprimate" in win.convert.warning.text()

    # dialoguri noi (construite, fără să le deschidem modal)
    from music_manager.ui.dialogs import (ArtistCleanupDialog, BulkEditDialog, GenreCleanupDialog,
                                          NameCleanupDialog, SmartPlaylistDialog, StyleMapDialog,
                                          TagFinderDialog)
    TagFinderDialog(tracks, None, win)
    NameCleanupDialog(tracks, win)
    BulkEditDialog(lv.items, win)
    GenreCleanupDialog(lv.items, win).auto
    ArtistCleanupDialog(lv.items, win)
    sd = SmartPlaylistDialog("", None, lambda r: len(ldb.smart_filter(lv.items, r)), win)
    sd._preview()
    StyleMapDialog(win)

    # curățenie
    win.cleanup._scanned(tracks)
    dlg = DuplicatesDialog(find_duplicates(tracks), win)
    assert dlg.tree.topLevelItemCount() == 1
    dlg.check_format("mp3")
    assert len(dlg._checked()) == 1            # niciodată toate piesele dintr-un grup
    dlg._set_all(False)
    assert dlg._checked() == []
    dlg.keep_only_mp3()
    groups = find_duplicates(tracks)
    has_mp3 = any(t.ext == "mp3" for t in groups[0])
    assert len(dlg._checked()) == (len(groups[0]) - 1 if has_mp3 else 0)
    rd = RenameDialog(tracks, win)
    assert rd.table.rowCount() == 4
    rd.plan_table._set_all(False)
    assert rd.plan_table.checked() == []
    rd.plan_table._count_clicked("conflict")

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

    # click pe playlist: arată ultima verificare salvată
    pp.save_rows()
    pp.current = None
    pp.show_playlist("p1")
    assert pp.current is not None and len(pp.rows) == 4

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


def test_jobs_start_now(app):
    import threading
    from music_manager.ui.jobs import QUEUED, RUNNING, Job, manager
    gate = threading.Event()
    a = Job("A", lambda p, c: gate.wait(5))
    b = Job("B", lambda p, c: 1)
    a.start()
    b.start()
    assert b.status == QUEUED and manager().blocker(b) is not None
    manager().start_now(b)
    assert b.status in (RUNNING, "done")
    gate.set()
    a.wait(3000)
    b.wait(3000)


def test_space_toggle(app):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
    from music_manager.ui.common import add_space_toggle
    t = QTableWidget(3, 2)
    for r in range(3):
        it = QTableWidgetItem()
        it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        it.setCheckState(Qt.Unchecked)
        t.setItem(r, 0, it)
        t.setItem(r, 1, QTableWidgetItem(str(r)))
    add_space_toggle(t, 0)
    t.selectRow(0)
    t.setSelectionMode(t.SelectionMode.MultiSelection)
    t.selectRow(2)
    app.sendEvent(t, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " "))
    assert [t.item(r, 0).checkState() == Qt.Checked for r in range(3)] == [True, False, True]
    app.sendEvent(t, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " "))
    assert all(t.item(r, 0).checkState() == Qt.Unchecked for r in range(3))


def test_player_tempo_and_dialogs(app):
    from music_manager.ui.library_dialogs import TraktorChangesDialog
    from music_manager.ui.player import PlayerPanel
    p = PlayerPanel()
    p.set_bpm(124)
    p.tempo.setValue(1000)                  # capăt de slider, Normal = +8%
    assert p.bpm_lbl.text() == "133.9 BPM"
    p.tempo_range.setCurrentIndex(1)        # Wide = +16%
    assert p.bpm_lbl.text() == "143.8 BPM"
    p.tempo.setValue(0)
    assert p.bpm_lbl.text() == "124.0 BPM"
    d = TraktorChangesDialog([("/m/a.mp3", "bpm", 62.0, 124.0), ("/m/a.mp3", "rating", 2, 4)])
    jobs = []
    d.on_write = jobs.extend
    d.write()
    assert jobs == [("/m/a.mp3", {"bpm": "124", "rating": "4"}, None)]
