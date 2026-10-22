"""Tabul „Curățenie”: dubluri, nume curate, taguri — pe un folder ales."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from ..core import library as lib
from .common import AppState, Task, TrackTable, data_dir, undo_dir
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
        self.table = TrackTable(self)
        self.search.textChanged.connect(self.table.proxy.set_text)

        b_dup = QPushButton("1. Găsește dubluri…")
        b_ren = QPushButton("2. Nume curate (Artist - Titlu)…")
        b_tags = QPushButton("3. Taguri: BPM, tonalitate, gen, an…")
        b_tags.setEnabled(False)
        b_tags.setToolTip("Urmează în versiunea următoare")
        self.b_undo = QPushButton("Anulează ultima operație")
        b_dup.clicked.connect(self.duplicates)
        b_ren.clicked.connect(self.rename)
        self.b_undo.clicked.connect(self.undo)
        actions = QHBoxLayout()
        for b in (b_dup, b_ren, b_tags):
            actions.addWidget(b)
        actions.addStretch()
        actions.addWidget(self.b_undo)
        hint = QLabel("Dacă selectezi piese în tabel, acțiunile se aplică doar lor; altfel tuturor celor afișate. "
                      "Nimic nu se șterge: dublurile se mută în „_Dubluri”, iar orice mutare se poate anula.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)
        lay.addWidget(self.search)
        lay.addWidget(self.table, 1)
        lay.addLayout(actions)
        lay.addWidget(hint)
        self._update_where()
        self._refresh_undo()

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

        self.task = Task(work, self)
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
        no_tags = sum(1 for t in tracks if not (t.artist and t.title))
        self.status.setText(f"<b>{len(tracks)}</b> piese · {no_tags} fără artist/titlu în taguri")

    def _targets(self) -> list[lib.Track]:
        return self.table.selected_tracks() or self.table.visible_tracks()

    def duplicates(self):
        if not self.tracks:
            QMessageBox.information(self, "Dubluri", "Scanează mai întâi.")
            return
        groups = lib.find_duplicates(self._targets())
        if not groups:
            QMessageBox.information(self, "Dubluri", "Nu am găsit dubluri.")
            return
        dlg = DuplicatesDialog(groups, self)
        if dlg.exec() == QDialog.Accepted and dlg.moved:
            self._after_change()

    def rename(self):
        tracks = self._targets()
        if not tracks:
            QMessageBox.information(self, "Nume curate", "Scanează mai întâi.")
            return
        dlg = RenameDialog(tracks, self)
        if dlg.exec() == QDialog.Accepted and dlg.applied:
            self._after_change()

    def _after_change(self):
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
        ok, fail, _ = lib.undo_last(undo_dir())
        QMessageBox.information(self, "Anulare", f"Am pus la loc {ok} fișiere." +
                                (f"\n{fail} nu au putut fi puse la loc." if fail else ""))
        self._after_change()
