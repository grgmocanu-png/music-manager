"""Tabul „Curățenie”: dubluri, nume curate, taguri — pe un folder ales."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from ..core import library as lib
from .common import COLUMNS, AppState, Task, TrackTable, data_dir, history, undo_dir
from .library_tab import DuplicatesDialog, RenameDialog
from .settings_tab import download_dir

TARGETS = ["Folderul de download (_unsorted)", "Toată biblioteca", "Alt folder…"]


class CleanupTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.task: Task | None = None
        self.tracks: list[lib.Track] = []
        self.other_dir = ""

        self.target = QComboBox()
        self.target.addItems(TARGETS)
        self.target.activated.connect(self._target_changed)
        self.where = QLabel()
        self.where.setStyleSheet("color: gray;")
        self.b_scan = QPushButton("Scanează")
        self.b_scan.clicked.connect(self.scan)
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Ce curăț:</b>"))
        top.addWidget(self.target)
        top.addWidget(self.b_scan)
        top.addWidget(self.where, 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status = QLabel("Alege ce vrei să cureți și apasă „Scanează”.")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrează…")
        self.search.setClearButtonEnabled(True)
        self.table = TrackTable(self, hidden_columns=("album",))
        self.table.source.status_fn = lambda p: history().label(p) or "—"
        self.table.setColumnWidth(len(COLUMNS) - 2, 220)
        self.search.textChanged.connect(self.table.proxy.set_text)
        self.only_todo = QCheckBox("Doar ce nu e curățat complet")
        self.only_todo.toggled.connect(lambda on: (self.table.proxy.set_only_unfinished(
            history().complete if on else None), self._update_status()))

        self.b_undo = QPushButton("↶ Anulează ultima operație")
        self.b_undo.clicked.connect(self.undo)
        top.addWidget(self.b_undo)

        # cei patru pași, ca niște carduri (ca în machetă)
        self.cards = {}
        steps = [
            ("dubluri", "Dubluri", "Piese care par la fel. „Păstrez doar MP3” lasă cel mai bun MP3 din fiecare grup.",
             "Caută dublurile", self.duplicates, "#e0a458"),
            ("nume", "Curăță nume", "Scoate linkuri, „free download”, „320kbps” și numere de piesă din nume și taguri.",
             "Curăță numele", self.clean_names, "#8fa8e0"),
            ("taguri", "Taguri online", "Gen, stil, an, label și copertă din Discogs, MusicBrainz și Spotify. "
             "Se scriu automat.", "Caută taguri", self.tags_online, "#5fb3a6"),
            ("redenumire", "Redenumire", "Pune fișierele în forma aleasă, ex. „Artist - Titlu”. Vezi fiecare "
             "conflict și motivul lui.", "Redenumește", self.rename, "#c79bd6"),
            ("sortare", "Sortează în foldere", "Familie → Gen (sau Familie / Familie → Stil), în orice folder. "
             "Copiază sau mută, cu previzualizare.", "Sortează…", self.sort_folders, "#9fe0d2"),
            ("conversie", "Conversie", "FLAC / WAV / AIFF → MP3 320 pentru Pioneer, sau invers. Păstrează "
             "tagurile și coperta.", "Deschide conversia", self.open_convert, "#d99b8c"),
        ]
        cards = QGridLayout()
        cards.setHorizontalSpacing(14)
        for col, (key, title, text, btn, fn, color) in enumerate(steps):
            card = QFrame()
            card.setObjectName("card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(16, 14, 16, 14)
            head = QHBoxLayout()
            t = QLabel(title)
            t.setStyleSheet("font-weight: 600; font-size: 14px;")
            n = QLabel("")
            n.setStyleSheet(f"color: {color}; font-weight: 600;")
            head.addWidget(t)
            head.addStretch()
            head.addWidget(n)
            d = QLabel(text)
            d.setWordWrap(True)
            d.setObjectName("muted")
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}")
            done = QLabel("")
            done.setObjectName("muted")
            b = QPushButton(btn)
            b.clicked.connect(fn)
            cl.addLayout(head)
            cl.addWidget(d)
            cl.addWidget(bar)
            cl.addWidget(done)
            cl.addWidget(b, 0, Qt.AlignLeft)
            cards.addWidget(card, col // 3, col % 3)
            self.cards[key] = (n, bar, done)
        hint = QLabel("Dacă selectezi piese în tabel, acțiunile se aplică doar lor; altfel tuturor celor afișate. "
                      "Nimic nu se șterge fără să întrebe: dublurile se mută în „_Dubluri” sau în Coș, iar orice "
                      "mutare se poate anula.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        title = QLabel("Curățenie")
        title.setObjectName("pageTitle")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        lay.setSpacing(12)
        tr = QHBoxLayout()
        tr.addWidget(title)
        tr.addSpacing(16)
        tr.addLayout(top, 1)
        lay.addLayout(tr)
        lay.addLayout(cards)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        srow = QHBoxLayout()
        srow.addWidget(self.only_todo)
        srow.addStretch()
        self.search.setFixedWidth(260)
        srow.addWidget(self.search)
        lay.addLayout(srow)
        lay.addWidget(self.table, 1)
        lay.addWidget(hint)
        self._update_where()
        self._refresh_undo()

    def selected_paths(self) -> list[str]:
        return [t.path for t in self.table.selected_tracks()]

    def focus_paths(self, paths):
        """Selectează în Curățenie piesele venite din Bibliotecă / Explore (dacă sunt în ce ai scanat)."""
        from ..core.djcollections import norm_path
        want = {norm_path(p) for p in paths}

        def rows():
            return [r for r in range(self.table.proxy.rowCount())
                    if norm_path(self.table.track_at(self.table.proxy.index(r, 0)).path) in want]
        found = rows()
        if not found and (self.search.text() or self.only_todo.isChecked()):
            self.search.clear()
            self.only_todo.setChecked(False)
            found = rows()
        if not found:
            if self.tracks:
                self.status.setText("Piesa selectată nu e în ce ai scanat aici (alege alt folder sus).")
            return
        from PySide6.QtCore import QItemSelection, QItemSelectionModel
        from PySide6.QtWidgets import QAbstractItemView
        selm = self.table.selectionModel()
        sel = QItemSelection()
        last = self.table.proxy.columnCount() - 1
        for r in found:
            sel.select(self.table.proxy.index(r, 0), self.table.proxy.index(r, last))
        selm.select(sel, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        ix = self.table.proxy.index(found[0], 0)
        selm.setCurrentIndex(ix, QItemSelectionModel.NoUpdate)
        self.table.scrollTo(ix, QAbstractItemView.PositionAtCenter)

    def sort_folders(self):
        tracks = self._targets()
        if not tracks:
            QMessageBox.information(self, "Sortează", "Scanează mai întâi.")
            return
        from .sort_tool import items_from_tracks, open_sort
        lv = getattr(self, "library", None)
        dlg = open_sort(items_from_tracks(tracks, lv), lv, self)
        if dlg is not None:
            dlg.on_applied = self._after_change

    def open_convert(self):
        """Conversia e acum un pas din Curățenie: se deschide pagina ei (cu „← Curățenie” sus)."""
        tracks = self._targets() if self.table.selectionModel().selectedRows() else []
        w = self.window()
        conv = getattr(w, "convert", None)
        if conv is None:
            return
        if tracks:
            self.state.send_to_convert.emit([t.path for t in tracks])
        elif hasattr(w, "show_tab"):
            w.show_tab(conv)

    def _target_changed(self, i):
        if TARGETS[i] == "Alt folder…":
            d = QFileDialog.getExistingDirectory(self, "Ce folder curăț?", self.other_dir)
            if d:
                self.other_dir = os.path.normpath(d)
        self._update_where()

    def folders(self) -> list[str]:
        t = self.target.currentText()
        if t.startswith("Folderul de download"):
            d = download_dir()
            return [d] if d else []
        if t == "Toată biblioteca":
            return list(self.state.folders)
        return [self.other_dir] if self.other_dir else []

    def _update_where(self):
        f = self.folders()
        self.where.setText("; ".join(f) if f else "— nu e ales niciun folder —")

    def scan(self):
        if self.task and self.task.isRunning():
            return
        self._update_where()
        folders = [f for f in self.folders() if os.path.isdir(f)]
        if not folders:
            QMessageBox.information(self, "Curățenie", "Folderul ales nu există sau nu e setat.")
            return
        cache = lib.LibraryCache(os.path.join(data_dir(), "biblioteca.json"))

        def work(progress, cancelled):
            return lib.scan(folders, cache, lambda d, t: progress(d, t), cancelled)

        from .jobs import Job
        self.task = Job("Scanez pentru curățenie", work, writes=False)
        self.task.progress.connect(lambda d, t, _: (self.progress.setMaximum(max(t, 1)), self.progress.setValue(d),
                                                    self.status.setText(f"Citesc: {d} / {t}")))
        self.task.succeeded.connect(self._scanned)
        self.task.failed.connect(lambda m: (self.progress.setVisible(False), QMessageBox.warning(self, "Eroare", m)))
        self.progress.setVisible(True)
        self.task.start()

    def _scanned(self, tracks):
        self.progress.setVisible(False)
        self.tracks = tracks
        self.table.set_tracks(tracks)
        self._update_status()

    def _update_cards(self):
        tracks = self.tracks
        n = len(tracks) or 1
        for key, (num, bar, done) in getattr(self, "cards", {}).items():
            if key in ("sortare", "conversie"):
                bar.setVisible(False)
                if key == "conversie":
                    nl = sum(1 for t in tracks if t.lossless)
                    num.setText(str(nl) if tracks else "")
                    done.setText(f"{nl} fișiere lossless (FLAC / WAV / AIFF)" if tracks else "")
                else:
                    done.setText(f"{len(tracks)} piese scanate" if tracks else "Scanează întâi")
                continue
            ok = sum(1 for t in tracks if history().done(t.path, key))
            todo = len(tracks) - ok
            num.setText(str(todo) if tracks else "")
            bar.setMaximum(n)
            bar.setValue(ok)
            done.setText(f"{ok} făcute · {todo} de făcut" if tracks else "Scanează întâi")

    def _update_status(self):
        self._update_cards()
        tracks = self.tracks
        no_tags = sum(1 for t in tracks if not (t.artist and t.title))
        done = sum(1 for t in tracks if history().complete(t.path))
        self.status.setText(f"<b>{len(tracks)}</b> piese · {no_tags} fără artist/titlu în taguri · "
                            f"{done} curățate complet · afișate: {self.table.proxy.rowCount()}")

    def _targets(self) -> list[lib.Track]:
        return self.table.selected_tracks() or self.table.visible_tracks()

    def duplicates(self):
        if not self.tracks:
            QMessageBox.information(self, "Dubluri", "Scanează mai întâi.")
            return
        targets = self._targets()
        groups = lib.find_duplicates(targets)
        if not groups:
            history().mark([t.path for t in targets], "dubluri")
            self.table.viewport().update()
            QMessageBox.information(self, "Dubluri", "Nu am găsit dubluri.")
            return
        dlg = DuplicatesDialog(groups, self, targets)
        dlg.on_applied = self._after_change
        from .windows import tool_window
        tool_window(dlg, f"Dubluri — {len(groups)} grupuri")

    def rename(self):
        tracks = self._targets()
        if not tracks:
            QMessageBox.information(self, "Nume curate", "Scanează mai întâi.")
            return
        dlg = RenameDialog(tracks, self)
        dlg.on_applied = self._after_change
        from .windows import tool_window
        tool_window(dlg)

    def clean_names(self):
        tracks = self._targets()
        if not tracks:
            QMessageBox.information(self, "Nume", "Scanează mai întâi.")
            return
        from .dialogs import NameCleanupDialog
        dlg = NameCleanupDialog(tracks, self)
        dlg.on_applied = self._after_change
        from .windows import tool_window
        tool_window(dlg)

    def tags_online(self):
        tracks = self._targets()
        if not tracks:
            QMessageBox.information(self, "Taguri", "Scanează mai întâi.")
            return
        from .dialogs import TagFinderDialog
        from .settings_tab import spotify_client
        dlg = TagFinderDialog(tracks, spotify_client(), self)
        dlg.on_applied = self._after_change
        from .windows import tool_window
        tool_window(dlg)    # fereastră separată: nu blochează, are buton în bara de task-uri

    def _after_change(self):
        self.table.viewport().update()
        self._refresh_undo()
        self.scan()

    def _refresh_undo(self):
        last = lib.last_undo(undo_dir())
        if last:
            self.b_undo.setText(f"Anulează ultima operație ({last[1].get('kind', '')}, "
                                f"{len(last[1].get('moves', []))} fișiere)")
            self.b_undo.setEnabled(True)
        else:
            self.b_undo.setText("Anulează ultima operație")
            self.b_undo.setEnabled(False)

    def undo(self):
        last = lib.last_undo(undo_dir())
        if not last:
            return
        if QMessageBox.question(self, "Anulare", f"Pun la loc fișierele mutate la ultima operație "
                                f"({last[1].get('kind')})?") != QMessageBox.Yes:
            return
        from .jobs import Job
        job = Job("Anulez ultima operație", lambda progress, cancelled: lib.undo_last(undo_dir()))
        job.succeeded.connect(lambda r: (QMessageBox.information(
            self, "Anulare", f"Am pus la loc {r[0]} fișiere." + (f"\n{r[1]} nu au putut fi puse la loc." if r[1] else "")),
            self._after_change()))
        job.start()
