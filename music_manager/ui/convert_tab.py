"""Tabul „Conversie”: FLAC/WAV/etc. → MP3, WAV, AIFF, FLAC."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from ..core import convert as conv
from ..core import tags as tagmod
from ..core.library import RENAME_PRESETS, find_audio_files
from .common import AppState, Task, add_reveal_menu, settings

SOURCE_FILTERS = {
    "Doar FLAC": {".flac"},
    "Toate fișierele fără pierderi (FLAC, WAV, AIFF, ALAC)": tagmod.LOSSLESS_EXTENSIONS | {".m4a"},
    "Toate fișierele audio": tagmod.AUDIO_EXTENSIONS,
}
IF_EXISTS_LABELS = {
    "Sari peste (nu face nimic)": "skip",
    "Păstrează-le pe amândouă (adaugă (2) la nume)": "rename",
    "Suprascrie": "overwrite",
}


class FileList(QListWidget):
    """Listă care acceptă fișiere și foldere trase cu mouse-ul."""

    def __init__(self, on_drop, parent=None):
        super().__init__(parent)
        self.on_drop = on_drop
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self.on_drop(paths)
        e.acceptProposedAction()


class ConvertTab(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.task: Task | None = None
        self.files: list[str] = []
        state.send_to_convert.connect(self.receive)

        # --- fișiere sursă ---
        self.list = FileList(self.add_paths)
        add_reveal_menu(self.list, lambda idx: self.list.item(idx.row()).text())
        self.source_filter = QComboBox()
        self.source_filter.addItems(SOURCE_FILTERS.keys())
        b_files = QPushButton("Adaugă fișiere…")
        b_folder = QPushButton("Adaugă folder…")
        b_remove = QPushButton("Scoate selecția")
        b_clear = QPushButton("Golește lista")
        b_files.clicked.connect(self.pick_files)
        b_folder.clicked.connect(self.pick_folder)
        b_remove.clicked.connect(self.remove_selected)
        b_clear.clicked.connect(self.clear)
        self.count = QLabel()

        src_btns = QHBoxLayout()
        for b in (b_files, b_folder, b_remove, b_clear):
            src_btns.addWidget(b)
        src_btns.addStretch()
        src_btns.addWidget(self.count)

        src_box = QGroupBox("1. Ce convertesc")
        sl = QVBoxLayout(src_box)
        fl = QHBoxLayout()
        fl.addWidget(QLabel("Din foldere iau:"))
        fl.addWidget(self.source_filter, 1)
        sl.addLayout(fl)
        sl.addWidget(QLabel("Poți trage fișiere sau foldere direct în listă, "
                            "sau le poți trimite din Bibliotecă."))
        sl.addWidget(self.list, 1)
        sl.addLayout(src_btns)

        # --- format ---
        self.preset = QComboBox()
        for key, p in conv.PRESETS.items():
            self.preset.addItem(p.label, key)
        self.rate = QComboBox()
        self.rate.addItems(conv.SAMPLE_RATES.keys())
        self.preset.currentIndexChanged.connect(self._update_warning)
        fmt_box = QGroupBox("2. În ce format")
        ff = QFormLayout(fmt_box)
        ff.addRow("Format:", self.preset)
        ff.addRow("Frecvență:", self.rate)
        self.naming = QComboBox()
        self.naming.addItem("Păstrează numele original", None)
        for label, tpl in RENAME_PRESETS.items():
            if "/" not in tpl:
                self.naming.addItem(label, tpl)
        saved = settings().value("convert/naming", "") or ""
        i = self.naming.findData(saved) if saved else -1
        self.naming.setCurrentIndex(i if i >= 0 else 1)
        self.naming.currentIndexChanged.connect(
            lambda: settings().setValue("convert/naming", self.naming.currentData() or ""))
        self.clean_names = QCheckBox("Curăță gunoaiele din nume (site-uri, [Free DL], 320kbps…)")
        self.clean_names.setChecked(True)
        ff.addRow("Nume fișier:", self.naming)
        ff.addRow("", self.clean_names)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #e67e22;")
        ff.addRow(self.warning)

        # --- destinație ---
        self.rb_next = QRadioButton("Lângă fișierul original")
        self.rb_folder = QRadioButton("Într-un folder separat:")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_next)
        grp.addButton(self.rb_folder)
        self.out_dir = QLineEdit(settings().value("convert/out_dir", "") or "")
        b_out = QPushButton("Alege…")
        b_out.clicked.connect(self.pick_out)
        self.keep_structure = QCheckBox("Păstrează structura de subfoldere")
        self.keep_structure.setChecked(True)
        self.if_exists = QComboBox()
        self.if_exists.addItems(IF_EXISTS_LABELS.keys())
        if self.out_dir.text():
            self.rb_folder.setChecked(True)
        else:
            self.rb_next.setChecked(True)

        dest_box = QGroupBox("3. Unde le salvez")
        dl = QVBoxLayout(dest_box)
        dl.addWidget(self.rb_next)
        r = QHBoxLayout()
        r.addWidget(self.rb_folder)
        r.addWidget(self.out_dir, 1)
        r.addWidget(b_out)
        dl.addLayout(r)
        dl.addWidget(self.keep_structure)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Dacă fișierul există deja:"))
        r2.addWidget(self.if_exists, 1)
        dl.addLayout(r2)
        dl.addWidget(QLabel("Originalele nu sunt modificate sau șterse."))

        # --- pornire ---
        self.b_start = QPushButton("Pornește conversia")
        self.b_start.setMinimumHeight(36)
        self.b_start.clicked.connect(self.start_or_stop)
        self.progress = QProgressBar()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)

        right = QVBoxLayout()
        right.addWidget(fmt_box)
        right.addWidget(dest_box)
        right.addWidget(self.b_start)
        right.addWidget(self.progress)
        right.addWidget(self.log, 1)

        lay = QHBoxLayout(self)
        lay.addWidget(src_box, 3)
        lay.addLayout(right, 2)

        if not conv.ffmpeg_path():
            self.log.appendPlainText("⚠ Nu găsesc ffmpeg — conversia nu va funcționa.")
        self._update_count()
        self._update_warning()

    # --- listă de fișiere ---
    def receive(self, paths: list[str]):
        self.add_paths(paths, apply_filter=False)
        show = getattr(self.window(), "show_tab", None)
        if show:
            show(self)

    def add_paths(self, paths: list[str], apply_filter: bool = True):
        allowed = SOURCE_FILTERS[self.source_filter.currentText()]
        existing = set(self.files)
        new = []
        for p in paths:
            if os.path.isdir(p):
                found = [f for f in find_audio_files([p])
                         if os.path.splitext(f)[1].lower() in allowed]
                new += found
            elif tagmod.is_audio_file(p):
                if not apply_filter or os.path.splitext(p)[1].lower() in tagmod.AUDIO_EXTENSIONS:
                    new.append(p)
        for p in new:
            p = os.path.normpath(p)
            if p not in existing:
                existing.add(p)
                self.files.append(p)
                self.list.addItem(p)
        self._update_count()
        self._update_warning()

    def pick_files(self):
        exts = " ".join("*" + e for e in sorted(tagmod.AUDIO_EXTENSIONS))
        paths, _ = QFileDialog.getOpenFileNames(self, "Alege fișiere audio", "",
                                                f"Fișiere audio ({exts})")
        if paths:
            self.add_paths(paths, apply_filter=False)

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Alege un folder")
        if d:
            self.add_paths([d])

    def remove_selected(self):
        for it in self.list.selectedItems():
            self.files.remove(it.text())
            self.list.takeItem(self.list.row(it))
        self._update_count()

    def clear(self):
        self.files.clear()
        self.list.clear()
        self._update_count()

    def _update_count(self):
        self.count.setText(f"{len(self.files)} fișiere")

    def _update_warning(self):
        preset = conv.PRESETS[self.preset.currentData()]
        lossy = [f for f in self.files if os.path.splitext(f)[1].lower() not in tagmod.LOSSLESS_EXTENSIONS]
        if preset.lossless and lossy:
            self.warning.setText(f"Atenție: {len(lossy)} fișiere sunt deja comprimate cu pierderi (ex. MP3). "
                                 "Convertite în WAV/AIFF/FLAC vor fi mai mari, dar nu vor suna mai bine.")
        else:
            self.warning.setText("")

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Unde salvez fișierele convertite?")
        if d:
            self.out_dir.setText(os.path.normpath(d))
            self.rb_folder.setChecked(True)

    # --- conversie ---
    def start_or_stop(self):
        if self.task and self.task.isRunning():
            self.task.cancel()
            self.b_start.setEnabled(False)
            self.b_start.setText("Se oprește după fișierele în lucru…")
            return
        self.start()

    def start(self):
        if not self.files:
            QMessageBox.information(self, "Conversie", "Adaugă mai întâi fișiere.")
            return
        out_dir = None
        if self.rb_folder.isChecked():
            out_dir = self.out_dir.text().strip()
            if not out_dir:
                QMessageBox.information(self, "Conversie", "Alege folderul de destinație.")
                return
            settings().setValue("convert/out_dir", out_dir)
        base = None
        if out_dir and self.keep_structure.isChecked() and len(self.files) > 1:
            try:
                base = os.path.commonpath([os.path.dirname(f) for f in self.files])
            except ValueError:
                base = None
        key = self.preset.currentData()
        rate = conv.SAMPLE_RATES[self.rate.currentText()]
        if_exists = IF_EXISTS_LABELS[self.if_exists.currentText()]
        jobs = conv.plan_jobs(list(self.files), key, out_dir, base,
                              name_template=self.naming.currentData(), clean_names=self.clean_names.isChecked())

        def work(progress, cancelled):
            def on_result(r, done, total):
                name = os.path.basename(r.src)
                if r.skipped:
                    txt = f"↷ {name}: {r.message}"
                elif r.ok:
                    txt = f"✓ {name} → {os.path.basename(r.dest)}" + (
                        "" if r.message == "Gata" else f" {r.message}")
                else:
                    txt = f"✗ {name}: {r.message}"
                progress(done, total, txt)
            return conv.convert_many(jobs, key, rate, if_exists, progress=on_result, cancelled=cancelled)

        self.log.appendPlainText(f"— Pornesc conversia a {len(jobs)} fișiere în "
                                 f"{conv.PRESETS[key].label.split(' (')[0]} —")
        from .jobs import Job
        self.task = Job(f"Conversie: {len(jobs)} fișiere", work)
        self.task.progress.connect(self._on_progress)
        self.task.succeeded.connect(self._on_done)
        self.task.failed.connect(self._on_failed)
        self.progress.setMaximum(len(jobs))
        self.progress.setValue(0)
        self.b_start.setText("Oprește")
        self.log.appendPlainText("(Dacă rulează altă activitate care modifică fișiere, conversia așteaptă să se termine.)")
        self.task.start()

    def _on_progress(self, done, total, txt):
        self.progress.setMaximum(total)
        self.progress.setValue(done)
        if txt:
            self.log.appendPlainText(txt)

    def _reset_button(self):
        self.b_start.setEnabled(True)
        self.b_start.setText("Pornește conversia")

    def _on_done(self, results):
        self._reset_button()
        ok = sum(1 for r in results if r.ok and not r.skipped)
        skipped = sum(1 for r in results if r.skipped)
        failed = sum(1 for r in results if not r.ok and not r.skipped)
        summary = f"Gata: {ok} convertite, {skipped} sărite, {failed} cu erori."
        self.log.appendPlainText("— " + summary + " —")
        if failed:
            QMessageBox.warning(self, "Conversie", summary + "\nDetaliile sunt în jurnalul din dreapta.")

    def _on_failed(self, msg):
        self._reset_button()
        self.log.appendPlainText("✗ Eroare: " + msg)
