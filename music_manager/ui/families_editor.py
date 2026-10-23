"""Setări → Genuri: familiile și stil → familie, editabile direct (fără fereastră ascunsă).

- familii: adaugi, redenumești (se schimbă și pe piesele care o au), ștergi, muți în ordine;
- stil → familie: tabel cu listă de alegere, căutare, „+ Stil”;
- stiluri nerecunoscute din biblioteca ta: le dai familia cu un click.
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core.genres import DEFAULT_FAMILIES, DEFAULT_STYLE_MAP
from .common import settings


def _load():
    from .dialogs import genre_mapper
    m = genre_mapper()
    return list(m.families), dict(m.style_map)


def _save(fams, mp):
    settings().setValue("genres/families", json.dumps(fams or DEFAULT_FAMILIES))
    settings().setValue("genres/style_map", json.dumps(mp or DEFAULT_STYLE_MAP))


def _library():
    try:
        from .library_view import _LIVE
        return _LIVE.get("library")
    except Exception:
        return None


class FamiliesEditor(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fams, self.map = _load()
        # --- familii ---
        self.list = QListWidget()
        self.list.setMaximumHeight(230)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        bf = QVBoxLayout()
        for text, fn in (("+ Familie nouă", self._add_family), ("Redenumește…", self._rename_family),
                         ("Șterge", self._delete_family), ("↑", lambda: self._move(-1)), ("↓", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bf.addWidget(b)
        bf.addStretch()
        frow = QHBoxLayout()
        frow.addWidget(self.list, 1)
        frow.addLayout(bf)
        # --- stil -> familie ---
        self.search = QLineEdit()
        self.search.setPlaceholderText("Caută un stil…")
        self.search.textChanged.connect(self._filter)
        b_style = QPushButton("+ Stil")
        b_style.clicked.connect(self._add_style)
        b_reset = QPushButton("Valorile implicite")
        b_reset.clicked.connect(self._reset)
        srow = QHBoxLayout()
        srow.addWidget(self.search, 1)
        srow.addWidget(b_style)
        srow.addWidget(b_reset)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Stil (cum apare în taguri / Discogs / Beatport)", "Familie"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 200)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(260)
        self.table.itemChanged.connect(self._style_renamed)
        # --- nerecunoscute ---
        self.unknown_label = QLabel()
        self.unknown_label.setWordWrap(True)
        self.unknown = QTableWidget(0, 3)
        self.unknown.setHorizontalHeaderLabels(["Stil / gen din biblioteca ta", "Piese", "Dă-i familia"])
        self.unknown.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.unknown.setColumnWidth(1, 60)
        self.unknown.setColumnWidth(2, 200)
        self.unknown.verticalHeader().setVisible(False)
        self.unknown.setMinimumHeight(200)
        b_scan = QPushButton("Reîmprospătează lista")
        b_scan.clicked.connect(self._fill_unknown)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("<b>Familiile</b> (genul „grosier” scris în tag; ordinea e cea din liste):"))
        lay.addLayout(frow)
        lay.addSpacing(8)
        lay.addWidget(QLabel("<b>Ce stil intră în ce familie</b>:"))
        lay.addLayout(srow)
        lay.addWidget(self.table, 1)
        lay.addSpacing(8)
        lay.addWidget(self.unknown_label)
        lay.addWidget(self.unknown)
        lay.addWidget(b_scan, 0, Qt.AlignLeft)
        self._fill()

    # --- afișare --------------------------------------------------------------
    def _combo(self, current, on_change):
        cb = QComboBox()
        cb.addItems(["—"] + self.fams)
        cb.setCurrentText(current if current in self.fams else "—")
        cb.currentTextChanged.connect(on_change)
        return cb

    def _fill(self):
        self.list.clear()
        self.list.addItems(self.fams)
        self.table.blockSignals(True)
        rows = sorted(self.map.items())
        self.table.setRowCount(len(rows))
        for i, (style, fam) in enumerate(rows):
            it = QTableWidgetItem(style)
            it.setData(Qt.UserRole, style)
            self.table.setItem(i, 0, it)
            self.table.setCellWidget(i, 1, self._combo(fam, lambda f, s=style: self._set_style(s, f)))
        self.table.blockSignals(False)
        self._filter(self.search.text())
        self._fill_unknown()

    def _filter(self, text):
        t = (text or "").lower().strip()
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            self.table.setRowHidden(i, bool(t) and t not in (it.text() if it else "").lower())

    def _fill_unknown(self):
        from ..core.genres import GenreMapper
        mapper = GenreMapper(self.fams, self.map)
        counts: dict[str, int] = {}
        lv = _library()
        for it in (lv.items if lv is not None else []):
            for g in [x.strip() for x in (it.track.genre or "").replace(";", ",").split(",")]:
                if g and not mapper.family_of(g):
                    counts[g] = counts.get(g, 0) + 1
        rows = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:300]
        self.unknown_label.setText(f"<b>Nerecunoscute</b>: {len(counts)} genuri din biblioteca ta nu intră în nicio "
                                   "familie. Alege familia și piesele lor o primesc imediat." if counts else
                                   "<b>Nerecunoscute</b>: toate genurile din bibliotecă au o familie. ✓")
        self.unknown.setRowCount(len(rows))
        for i, (g, n) in enumerate(rows):
            self.unknown.setItem(i, 0, QTableWidgetItem(g))
            c = QTableWidgetItem(str(n))
            c.setTextAlignment(Qt.AlignCenter)
            self.unknown.setItem(i, 1, c)
            self.unknown.setCellWidget(i, 2, self._combo("", lambda f, s=g: self._assign_unknown(s, f)))

    # --- modificări --------------------------------------------------------------
    def _commit(self, refill=True):
        _save(self.fams, self.map)
        if refill:
            self._fill()
        self.changed.emit()
        lv = _library()
        if lv is not None:
            try:
                lv._annotate(lv.items)
                lv.rebuild_tree()
                lv.show_view(lv.view)
            except Exception:
                pass

    def _set_style(self, style, fam):
        if fam == "—":
            self.map.pop(style.lower(), None)
        else:
            self.map[style.lower()] = fam
        self._commit(refill=False)
        self._fill_unknown()

    def _assign_unknown(self, style, fam):
        if fam == "—":
            return
        self.map[style.lower()] = fam
        self._commit()

    def _style_renamed(self, item):
        if item.column() != 0:
            return
        old = item.data(Qt.UserRole)
        new = item.text().strip().lower()
        if not new or new == old:
            return
        fam = self.map.pop(old, "") if old else ""
        if fam:
            self.map[new] = fam
        self._commit()

    def _add_style(self):
        s, ok = QInputDialog.getText(self, "Stil nou", "Stilul (ex. Melodic House & Techno):")
        if not ok or not s.strip():
            return
        fam, ok = QInputDialog.getItem(self, "Stil nou", f"„{s.strip()}” intră în familia:", self.fams, 0, False)
        if ok and fam:
            self.map[s.strip().lower()] = fam
            self._commit()

    def _add_family(self):
        name, ok = QInputDialog.getText(self, "Familie nouă", "Numele familiei (ex. Reggae & Dub):")
        name = (name or "").strip()
        if ok and name and name.lower() not in (f.lower() for f in self.fams):
            self.fams.append(name)
            self._commit()

    def _current(self):
        it = self.list.currentItem()
        return it.text() if it else ""

    def _rename_family(self):
        old = self._current()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Redenumește familia", f"Noul nume pentru „{old}”:", text=old)
        new = (new or "").strip()
        if not ok or not new or new == old:
            return
        self.fams = [new if f == old else f for f in self.fams]
        self.map = {k: (new if v == old else v) for k, v in self.map.items()}
        lv = _library()
        n = 0
        if lv is not None:       # familia pusă de mână pe piese
            for e in lv.db.extras.values():
                if getattr(e, "user_family", "") == old:
                    e.user_family = new
                    n += 1
            lv.db.save()
        self._commit()
        if n:
            QMessageBox.information(self, "Familie", f"Am schimbat familia și la {n} piese unde o puseseși de mână.")

    def _delete_family(self):
        f = self._current()
        if not f:
            return
        n = sum(1 for v in self.map.values() if v == f)
        if QMessageBox.question(self, "Șterge familia", f"Șterg familia „{f}”? {n} stiluri rămân fără familie, iar "
                                "piesele lor apar ca nerecunoscute.") != QMessageBox.Yes:
            return
        self.fams = [x for x in self.fams if x != f]
        self.map = {k: v for k, v in self.map.items() if v != f}
        self._commit()

    def _move(self, step):
        i = self.list.currentRow()
        j = i + step
        if i < 0 or not 0 <= j < len(self.fams):
            return
        self.fams[i], self.fams[j] = self.fams[j], self.fams[i]
        self._commit()
        self.list.setCurrentRow(j)

    def _reset(self):
        if QMessageBox.question(self, "Genuri", "Revin la familiile și stilurile implicite?") == QMessageBox.Yes:
            self.fams, self.map = list(DEFAULT_FAMILIES), {k.lower(): v for k, v in DEFAULT_STYLE_MAP.items()}
            self._commit()
