"""Căutare manuală pe Soulseek: scrii ce vrei, vezi rezultatele, alegi fișierul."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..core import soulseek as slsk
from .common import Task, data_dir, fmt_duration, fmt_size, settings
from .settings_tab import slsk_credentials

COLS = ["Fișier", "Format", "kbps", "Durată", "Mărime", "Viteză", "Slot liber", "Utilizator", "Folder"]


class NumItem(QTableWidgetItem):
    """Celulă care se sortează după număr, nu după text."""

    def __init__(self, text, value):
        super().__init__(text)
        self.value = value

    def __lt__(self, other):
        if isinstance(other, NumItem):
            return self.value < other.value
        return super().__lt__(other)


class ManualSearchDialog(QDialog):
    """Întoarce rezultatul ales în self.chosen (SearchResult) după accept()."""

    def __init__(self, query: str, track_label: str = "", expected_length: float = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Caută manual pe Soulseek" + (f" — {track_label}" if track_label else ""))
        self.resize(1150, 620)
        self.expected = expected_length
        self.chosen: slsk.SearchResult | None = None
        self.results: list[slsk.SearchResult] = []
        self.task: Task | None = None

        self.query = QLineEdit(query)
        self.query.returnPressed.connect(self.search)
        self.b_search = QPushButton("Caută")
        self.b_search.setDefault(True)
        self.b_search.clicked.connect(self.search)
        top = QHBoxLayout()
        top.addWidget(QLabel("Caută:"))
        top.addWidget(self.query, 1)
        top.addWidget(self.b_search)
        tip = QLabel("Sfat: dacă nu găsești cu titlul complet, încearcă artistul + un cuvânt distinctiv din titlu. "
                     "Durează ~10 secunde (programul se conectează, caută și așteaptă răspunsurile).")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")

        self.only_good = QCheckBox("Doar MP3 ≥ 256 kbps și fișiere fără pierderi")
        self.only_good.setChecked(True)
        self.only_good.toggled.connect(self.fill)
        self.only_len = QCheckBox("Doar cu durata apropiată (±10 s)")
        self.only_len.setChecked(expected_length > 0)
        self.only_len.setEnabled(expected_length > 0)
        self.only_len.toggled.connect(self.fill)
        filt = QHBoxLayout()
        filt.addWidget(self.only_good)
        filt.addWidget(self.only_len)
        filt.addStretch()
        self.count = QLabel()
        filt.addWidget(self.count)

        self.progress = QProgressBar()
        self.progress.setMaximum(0)
        self.progress.setVisible(False)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        for c, w in enumerate((330, 55, 50, 60, 70, 70, 70, 120)):
            self.table.setColumnWidth(c, w)
        self.table.doubleClicked.connect(lambda _: self.take())

        self.b_take = QPushButton("Descarcă fișierul selectat")
        self.b_take.clicked.connect(self.take)
        close = QPushButton("Închide")
        close.clicked.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addStretch()
        bottom.addWidget(close)
        bottom.addWidget(self.b_take)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(tip)
        lay.addLayout(filt)
        lay.addWidget(self.progress)
        lay.addWidget(self.table, 1)
        lay.addLayout(bottom)

    def search(self):
        q = self.query.text().strip()
        if not q or (self.task and self.task.isRunning()):
            return
        user, pw = slsk_credentials()
        if not user or not pw:
            QMessageBox.information(self, "Soulseek", "Completează utilizatorul și parola de Soulseek în Setări.")
            return
        exe = slsk.sockseek_path(settings().value("slsk/sockseek_path", "") or "")
        work = os.path.join(data_dir(), "soulseek")

        def work_fn(progress, cancelled):
            return slsk.search(q, user, pw, exe, work)

        self.task = Task(work_fn, self)
        self.task.succeeded.connect(self._done)
        self.task.failed.connect(self._failed)
        self.progress.setVisible(True)
        self.b_search.setEnabled(False)
        self.count.setText("Caut…")
        self.task.start()

    def _done(self, results):
        self.progress.setVisible(False)
        self.b_search.setEnabled(True)
        self.results = results
        self.fill()

    def _failed(self, msg):
        self.progress.setVisible(False)
        self.b_search.setEnabled(True)
        self.count.setText("")
        QMessageBox.warning(self, "Soulseek", msg)

    def _visible(self) -> list[slsk.SearchResult]:
        out = []
        for r in self.results:
            if self.only_good.isChecked():
                lossless = r.ext in ("flac", "wav", "aiff", "aif")
                if not lossless and not (r.ext == "mp3" and r.bitrate >= 256):
                    continue
            if self.only_len.isChecked() and self.expected and r.length:
                if abs(r.length - self.expected) > 10:
                    continue
            out.append(r)
        return out

    def fill(self):
        rows = self._visible()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            name = QTableWidgetItem(r.name)
            name.setData(Qt.UserRole, r)
            name.setToolTip(r.filename)
            self.table.setItem(i, 0, name)
            self.table.setItem(i, 1, QTableWidgetItem(r.ext.upper()))
            self.table.setItem(i, 2, NumItem(str(r.bitrate) if r.bitrate else "?", r.bitrate))
            dur = NumItem(fmt_duration(r.length) if r.length else "?", r.length)
            if self.expected and r.length and abs(r.length - self.expected) > 10:
                dur.setForeground(QBrush(QColor("#e67e22")))
            self.table.setItem(i, 3, dur)
            self.table.setItem(i, 4, NumItem(fmt_size(r.size), r.size))
            self.table.setItem(i, 5, NumItem(f"{r.speed:.1f} MB/s", r.speed))
            slot = NumItem("✓" if r.free_slot else "✗ coadă", int(r.free_slot))
            slot.setForeground(QBrush(QColor("#27ae60" if r.free_slot else "#c0392b")))
            self.table.setItem(i, 6, slot)
            self.table.setItem(i, 7, QTableWidgetItem(r.username))
            self.table.setItem(i, 8, QTableWidgetItem(r.folder))
        self.table.setSortingEnabled(True)
        self.count.setText(f"{len(rows)} rezultate afișate din {len(self.results)}")

    def take(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Soulseek", "Alege un rezultat din listă.")
            return
        self.chosen = self.table.item(row, 0).data(Qt.UserRole)
        self.accept()
