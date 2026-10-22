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
    AppState, Task, TrackTable, data_dir, fmt_duration, fmt_size, reveal_in_folder,
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
        b_show = QPushButton("Arată în folder")
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

        self.task = Task(work, self)
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


class DuplicatesDialog(QDialog):
    def __init__(self, groups: list[list[lib.Track]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dubluri")
        self.resize(1000, 600)
        self.moved = False

        info = QLabel(
            f"Am găsit <b>{len(groups)}</b> grupuri de piese care par la fel (același artist și titlu, "
            "durată apropiată). În fiecare grup, prima e cea de cea mai bună calitate și rămâne "
            "nebifată. <b>Bifatele</b> vor fi mutate într-un folder <i>_Dubluri</i> lângă ele — "
            "nimic nu se șterge, iar mutarea se poate anula.")
        info.setWordWrap(True)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Piesă", "Calitate", "Durată", "Cale"])
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(1, 150)
        self.tree.setColumnWidth(2, 60)
        for g in groups:
            first = g[0]
            parent_item = QTreeWidgetItem([f"{first.artist or '?'} – {first.display_title}  ({len(g)})"])
            f = parent_item.font(0)
            f.setBold(True)
            parent_item.setFont(0, f)
            self.tree.addTopLevelItem(parent_item)
            for i, t in enumerate(g):
                it = QTreeWidgetItem([t.filename, t.quality_label, fmt_duration(t.duration),
                                      os.path.dirname(t.path)])
                it.setData(0, Qt.UserRole, t.path)
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Unchecked if i == 0 else Qt.Checked)
                it.setToolTip(3, t.path)
                if i == 0:
                    it.setForeground(1, QBrush(QColor("#27ae60")))
                parent_item.addChild(it)
            parent_item.setExpanded(True)

        self.btn_move = QPushButton("Mută bifatele în _Dubluri")
        self.btn_move.clicked.connect(self.move)
        close = QPushButton("Închide")
        close.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        row.addWidget(self.btn_move)

        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.tree, 1)
        lay.addLayout(row)

    def _checked(self) -> list[str]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            p = self.tree.topLevelItem(i)
            for j in range(p.childCount()):
                c = p.child(j)
                if c.checkState(0) == Qt.Checked:
                    out.append(c.data(0, Qt.UserRole))
        return out

    def move(self):
        paths = self._checked()
        if not paths:
            return
        if QMessageBox.question(self, "Confirmare",
                                f"Mut {len(paths)} fișiere în foldere „_Dubluri”?") != QMessageBox.Yes:
            return
        moves, errors = lib.move_to_duplicates(paths, undo_dir())
        self.moved = True
        report(self, f"Am mutat {len(moves)} fișiere.", errors)
        self.accept()


class RenameDialog(QDialog):
    STATUS = {
        "ok": ("Se redenumește", "#27ae60"),
        "same": ("Deja corect", "gray"),
        "missing": ("Lipsește artistul sau titlul — sărit", "#e67e22"),
        "conflict": ("Ar suprascrie alt fișier — sărit", "#c0392b"),
    }

    def __init__(self, tracks: list[lib.Track], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Redenumește / organizează — {len(tracks)} piese")
        self.resize(1100, 650)
        self.tracks = tracks
        self.applied = False
        self.plan: list[lib.RenameItem] = []

        self.preset = QComboBox()
        self.preset.addItems(list(lib.RENAME_PRESETS.keys()) + ["Personalizat"])
        self.template = QLineEdit(lib.RENAME_PRESETS["Artist - Titlu"])
        self.template.setToolTip(
            "Câmpuri disponibile: {artist} {title} {album} {albumartist} {genre} {year} "
            "{track:02} {bpm} {key}\nUn „/” înseamnă folder nou.")
        self.base = QLineEdit()
        self.base.setPlaceholderText("Folderul în care se creează structura (obligatoriu când șablonul are „/”)")
        b_base = QPushButton("Alege…")
        b_base.clicked.connect(self._choose_base)

        self.preset.currentTextChanged.connect(self._on_preset)
        self.template.textEdited.connect(lambda _: self.preset.setCurrentText("Personalizat"))
        self.template.textChanged.connect(self.refresh)
        self.base.textChanged.connect(self.refresh)

        form = QVBoxLayout()
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Model:"))
        r1.addWidget(self.preset)
        r1.addWidget(QLabel("Șablon:"))
        r1.addWidget(self.template, 1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Organizează în:"))
        r2.addWidget(self.base, 1)
        r2.addWidget(b_base)
        form.addLayout(r1)
        form.addLayout(r2)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Acum", "Devine", "Stare"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.summary = QLabel()
        self.buttons = QDialogButtonBox()
        self.btn_apply = self.buttons.addButton("Aplică", QDialogButtonBox.AcceptRole)
        self.buttons.addButton("Renunță", QDialogButtonBox.RejectRole)
        self.buttons.accepted.connect(self.apply)
        self.buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.summary)
        lay.addWidget(self.buttons)
        self.refresh()

    def _on_preset(self, name):
        if name in lib.RENAME_PRESETS:
            self.template.setText(lib.RENAME_PRESETS[name])

    def _choose_base(self):
        d = QFileDialog.getExistingDirectory(self, "Unde creez structura de foldere?")
        if d:
            self.base.setText(os.path.normpath(d))

    def refresh(self):
        tpl = self.template.text().strip()
        uses_folders = "/" in tpl
        self.base.setEnabled(uses_folders)
        base = self.base.text().strip() or None
        if not tpl or (uses_folders and not base):
            self.plan = []
            self.table.setRowCount(0)
            self.summary.setText("Alege folderul în care să creez structura." if uses_folders else "")
            self.btn_apply.setEnabled(False)
            return
        self.plan = lib.plan_renames(self.tracks, tpl, base)
        shown = self.plan[:2000]
        self.table.setRowCount(len(shown))
        common = os.path.commonpath([p.src for p in self.plan] + [p.dest for p in self.plan]) if self.plan else ""
        for r, item in enumerate(shown):
            label, color = self.STATUS[item.status]
            a = QTableWidgetItem(os.path.relpath(item.src, common) if common else item.src)
            b = QTableWidgetItem(os.path.relpath(item.dest, common) if item.status != "missing" else "")
            c = QTableWidgetItem(label)
            c.setForeground(QBrush(QColor(color)))
            a.setToolTip(item.src)
            b.setToolTip(item.dest)
            for col, w in enumerate((a, b, c)):
                self.table.setItem(r, col, w)
        counts = {k: sum(1 for p in self.plan if p.status == k) for k in self.STATUS}
        txt = (f"<b>{counts['ok']}</b> se redenumesc · {counts['same']} deja corecte · "
               f"{counts['missing']} fără taguri · {counts['conflict']} conflicte")
        if len(self.plan) > len(shown):
            txt += f" · (se afișează primele {len(shown)})"
        self.summary.setText(txt)
        self.btn_apply.setEnabled(counts["ok"] > 0)

    def apply(self):
        n = sum(1 for p in self.plan if p.status == "ok")
        if QMessageBox.question(self, "Confirmare", f"Redenumesc {n} fișiere?\n"
                                "Operația se poate anula din butonul „Anulează ultima operație”.") != QMessageBox.Yes:
            return
        moves, errors = lib.apply_renames(self.plan, undo_dir())
        self.applied = True
        report(self, f"Am redenumit {len(moves)} fișiere.", errors)
        self.accept()
