"""Tabul „Bibliotecă”: foldere, scanare, căutare, dubluri, redenumire."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import library as lib
from .common import (
    AppState, Task, TrackTable, data_dir, fmt_duration, fmt_size, reveal_in_folder, reveal_label,
    undo_dir,
)


class LibraryTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.task: Task | None = None

        # --- foldere ---
        self.folder_list = QListWidget()
        self.folder_list.setMaximumHeight(80)
        self.folder_list.addItems(state.folders)
        b_add = QPushButton("Adaugă folder…")
        b_add.setToolTip("Folderele cu muzica ta de mix. Se scanează cu tot cu subfoldere.")
        b_del = QPushButton("Scoate folderul")
        self.b_scan = QPushButton("Scanează")
        self.b_scan.setDefault(True)
        b_add.clicked.connect(self.add_folder)
        b_del.clicked.connect(self.remove_folder)
        self.b_scan.clicked.connect(self.scan_or_cancel)

        fbtn = QVBoxLayout()
        for b in (b_add, b_del, self.b_scan):
            fbtn.addWidget(b)
        fbtn.addStretch()
        top = QHBoxLayout()
        flay = QVBoxLayout()
        flay.addWidget(QLabel("<b>Folderele cu muzica ta</b> (se scanează cu tot cu subfolderele)"))
        flay.addWidget(self.folder_list)
        top.addLayout(flay, 1)
        top.addLayout(fbtn)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status = QLabel("Adaugă un folder cu muzică și apasă „Scanează”.")

        # --- căutare + tabel ---
        self.search = QLineEdit()
        self.search.setPlaceholderText("Caută după artist, titlu, album, gen…")
        self.search.setClearButtonEnabled(True)
        self.table = TrackTable(self)
        self.search.textChanged.connect(self._on_search)

        # --- acțiuni ---
        b_conv = QPushButton("Trimite la Conversie")
        b_pl = QPushButton("Adaugă în playlist")
        b_show = QPushButton(reveal_label())
        b_conv.clicked.connect(lambda: self._send(self.state.send_to_convert, paths=True))
        b_pl.clicked.connect(lambda: self._send(self.state.send_to_playlist, paths=False))
        b_show.clicked.connect(self.show_in_folder)

        actions = QHBoxLayout()
        for b in (b_conv, b_pl, b_show):
            actions.addWidget(b)
        actions.addStretch()

        hint = QLabel("Sfat: selectezi mai multe piese cu Shift sau Ctrl/Cmd. "
                      "Dacă nu selectezi nimic, acțiunile se aplică tuturor pieselor afișate. "
                      "Dublu-click pornește piesa.")
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

        if state.folders:
            self.scan()

    # --- foldere / scanare ---
    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Alege un folder cu muzică")
        if not d:
            return
        d = os.path.normpath(d)
        if d in self.state.folders:
            return
        self.state.set_folders(self.state.folders + [d])
        self.folder_list.addItem(d)
        self.scan()

    def remove_folder(self):
        item = self.folder_list.currentItem()
        if not item:
            return
        folders = [f for f in self.state.folders if f != item.text()]
        self.state.set_folders(folders)
        self.folder_list.takeItem(self.folder_list.row(item))
        self.scan()

    def scan_or_cancel(self):
        if self.task and self.task.isRunning():
            self.task.cancel()
            self.b_scan.setEnabled(False)
        else:
            self.scan()

    def scan(self):
        if self.task and self.task.isRunning():
            return
        folders = [f for f in self.state.folders if os.path.isdir(f)]
        missing = [f for f in self.state.folders if not os.path.isdir(f)]
        if not folders:
            self.state.set_tracks([])
            self.table.set_tracks([])
            self.status.setText("Niciun folder disponibil." + (
                f" Lipsesc: {', '.join(missing)} (poate e un disc extern deconectat?)" if missing else ""))
            return
        cache = lib.LibraryCache(os.path.join(data_dir(), "biblioteca.json"))

        def work(progress, cancelled):
            return lib.scan(folders, cache, lambda d, t: progress(d, t), cancelled)

        from .jobs import Job
        self.task = Job("Scanez biblioteca", work, writes=False)
        self.task.progress.connect(self._on_progress)
        self.task.succeeded.connect(lambda tracks: self._on_scanned(tracks, missing))
        self.task.failed.connect(self._on_failed)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.b_scan.setText("Oprește")
        self.status.setText("Caut fișiere audio…")
        self.task.start()

    def _on_progress(self, done, total, _txt):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        self.status.setText(f"Citesc tagurile: {done} / {total}")

    def _on_scanned(self, tracks, missing):
        self.progress.setVisible(False)
        self.b_scan.setText("Scanează")
        self.b_scan.setEnabled(True)
        self.state.set_tracks(tracks)
        self.table.set_tracks(tracks)
        size = sum(t.size for t in tracks)
        dur = sum(t.duration for t in tracks)
        errs = sum(1 for t in tracks if t.error)
        no_tags = sum(1 for t in tracks if not (t.artist and t.title))
        parts = [f"<b>{len(tracks)}</b> piese", fmt_size(size), f"{fmt_duration(dur)} de muzică"]
        if no_tags:
            parts.append(f"{no_tags} fără artist/titlu")
        if errs:
            parts.append(f"<span style='color:#c0392b'>{errs} nu au putut fi citite (cu roșu)</span>")
        if missing:
            parts.append(f"<span style='color:#c0392b'>folder lipsă: {', '.join(missing)}</span>")
        self.status.setText(" · ".join(parts))

    def _on_failed(self, msg):
        self.progress.setVisible(False)
        self.b_scan.setText("Scanează")
        self.b_scan.setEnabled(True)
        QMessageBox.warning(self, "Eroare", f"Scanarea a eșuat:\n{msg}")

    def _on_search(self, text):
        self.table.proxy.set_text(text)

    # --- utilitare ---
    def _target_tracks(self) -> list[lib.Track]:
        return self.table.selected_tracks() or self.table.visible_tracks()

    def _send(self, signal, paths: bool):
        tracks = self._target_tracks()
        if not tracks:
            return
        signal.emit([t.path for t in tracks] if paths else tracks)

    def show_in_folder(self):
        sel = self.table.selected_tracks()
        if sel:
            reveal_in_folder(sel[0].path)


def report(parent, message: str, errors: list[str]):
    if errors:
        box = QMessageBox(QMessageBox.Warning, "Gata, cu probleme",
                          message + f"\n{len(errors)} fișiere nu au putut fi mutate "
                          "(poate sunt deschise în alt program).", parent=parent)
        box.setDetailedText("\n".join(errors))
        box.exec()
    else:
        QMessageBox.information(parent, "Gata", message)


from .cleanup_dialogs import DuplicatesDialog, RenameDialog  # noqa: E402,F401  (mutate în cleanup_dialogs)
