"""Ferestre din Bibliotecă: „Curăță categorii” și căutarea unei piese lipsă în bibliotecă."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)

from ..core import categories as cat
from ..core import libdb as ldb
from .common import add_space_toggle, fmt_duration
from .dialogs import _item, _table, write_tags_in_background


class CategoryCleanupDialog(QDialog):
    """Toate valorile unui câmp (ex. Comments), cu câte piese au fiecare valoare.
    Poți șterge un text, înlocui un text sau uni mai multe valori într-una."""

    def __init__(self, items, field: str, parent=None):
        super().__init__(parent)
        self.items = [it for it in items if not it.missing and not it.offline]
        self.field = field
        self.title = cat.FIELDS[field]
        self.on_applied = None
        self.setWindowTitle(f"Curăță categorii — {self.title}")
        self.resize(820, 640)

        info = QLabel(f"Toate valorile din <b>{self.title}</b>, cu numărul de piese. Alege una dintre operațiile "
                      "de jos; înainte să scriu în fișiere îți arăt câte piese se schimbă.")
        info.setWordWrap(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filtrează valorile…")
        self.search.textChanged.connect(self._filter)
        self.table = _table(["", f"{self.title}", "Piese"], (28, 560))
        add_space_toggle(self.table, 0)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)

        # 1. șterge text
        g1 = QGroupBox("Șterge un text din toate valorile")
        self.del_text = QLineEdit()
        self.del_text.setPlaceholderText("ex. Danceability")
        self.del_nums = QCheckBox("împreună cu cifrele lipite de el (ex. „04 Danceability”)")
        self.del_nums.setChecked(True)
        b_del = QPushButton("Șterge…")
        b_del.clicked.connect(self.do_delete)
        h1 = QHBoxLayout(g1)
        h1.addWidget(self.del_text, 1)
        h1.addWidget(self.del_nums)
        h1.addWidget(b_del)

        # 2. înlocuiește
        g2 = QGroupBox("Înlocuiește un text în toate valorile")
        self.rep_old = QLineEdit()
        self.rep_old.setPlaceholderText("ce caut")
        self.rep_new = QLineEdit()
        self.rep_new.setPlaceholderText("cu ce înlocuiesc (gol = șterge)")
        b_rep = QPushButton("Înlocuiește…")
        b_rep.clicked.connect(self.do_replace)
        h2 = QHBoxLayout(g2)
        h2.addWidget(self.rep_old, 1)
        h2.addWidget(QLabel("→"))
        h2.addWidget(self.rep_new, 1)
        h2.addWidget(b_rep)

        # 3. unește
        g3 = QGroupBox("Unește valorile bifate într-una singură")
        self.merge_to = QComboBox()
        self.merge_to.setEditable(True)
        b_merge = QPushButton("Unește…")
        b_merge.clicked.connect(self.do_merge)
        h3 = QHBoxLayout(g3)
        h3.addWidget(QLabel("Devin:"))
        h3.addWidget(self.merge_to, 1)
        h3.addWidget(b_merge)

        bb = QDialogButtonBox()
        bb.addButton("Închide", QDialogButtonBox.RejectRole)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.search)
        lay.addWidget(self.table, 1)
        lay.addWidget(g1)
        lay.addWidget(g2)
        lay.addWidget(g3)
        if field == "artist":
            b_var = QPushButton("Găsește artiștii scriși în mai multe feluri…")
            b_var.clicked.connect(self._artist_variants)
            lay.addWidget(b_var)
        lay.addWidget(bb)
        self.table.itemChanged.connect(self._checked_changed)
        self.fill()

    def fill(self):
        self.counts = cat.value_counts(getattr(it.track, self.field, "") for it in self.items)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.counts))
        for i, (v, n) in enumerate(self.counts):
            self.table.setItem(i, 0, _item("", check=False))
            self.table.setItem(i, 1, _item(v or "(gol)", "gray" if not v else None, tooltip=v))
            self.table.setItem(i, 2, _item(str(n)))
        self.table.blockSignals(False)
        self.merge_to.clear()
        self.merge_to.addItems([v for v, _ in self.counts[:200] if v])
        self.merge_to.setCurrentText("")
        self._filter(self.search.text())

    def _filter(self, text):
        t = text.lower().strip()
        for i, (v, _n) in enumerate(self.counts):
            self.table.setRowHidden(i, bool(t) and t not in v.lower())

    def _checked_values(self) -> list[str]:
        return [self.counts[i][0] for i in range(self.table.rowCount())
                if self.table.item(i, 0).checkState() == Qt.Checked]

    def _checked_changed(self, item):
        if item.column() == 0 and item.checkState() == Qt.Checked and not self.merge_to.currentText():
            self.merge_to.setCurrentText(self.counts[item.row()][0])

    def _apply(self, changes, what):
        if not changes:
            QMessageBox.information(self, self.title, "Nicio piesă nu se schimbă.")
            return
        examples = "\n".join(f"„{o}”  →  „{n or '(gol)'}”" for _p, o, n in changes[:8])
        more = f"\n… și încă {len(changes) - 8}" if len(changes) > 8 else ""
        if QMessageBox.question(self, self.title, f"{what}\n\nSe schimbă {len(changes)} piese, de exemplu:\n\n"
                                f"{examples}{more}\n\nScriu în fișiere?") != QMessageBox.Yes:
            return
        jobs = [(p, {self.field: n}, None) for p, _o, n in changes]
        by_path = {it.path: it for it in self.items}
        for p, _o, n in changes:                 # actualizăm imediat și ce vedem
            it = by_path.get(p)
            if it is not None:
                setattr(it.track, self.field, n)
        write_tags_in_background(self.parent(), jobs, self.on_applied or (lambda *_: None),
                                 f"Curăță {self.title}")
        self.fill()

    def do_delete(self):
        text = self.del_text.text().strip()
        if not text:
            return
        nums = self.del_nums.isChecked()
        self._apply(cat.plan(self.items, self.field, lambda v: cat.remove_text(v, text, nums)),
                    f"Șterg „{text}” din {self.title}.")

    def do_replace(self):
        old, new = self.rep_old.text(), self.rep_new.text()
        if not old:
            return
        self._apply(cat.plan(self.items, self.field, lambda v: cat.replace_text(v, old, new)),
                    f"Înlocuiesc „{old}” cu „{new}” în {self.title}.")

    def do_merge(self):
        vals = set(self._checked_values())
        target = self.merge_to.currentText().strip()
        if not vals:
            QMessageBox.information(self, self.title, "Bifează întâi valorile pe care vrei să le unești.")
            return
        self._apply(cat.plan(self.items, self.field, lambda v: target if v in vals else v),
                    f"{len(vals)} valori devin „{target or '(gol)'}”.")

    def _artist_variants(self):
        from .dialogs import ArtistCleanupDialog
        dlg = ArtistCleanupDialog(self.items, self.parent())
        if not dlg.groups:
            QMessageBox.information(self, "Artiști", "Nu am găsit artiști scriși în mai multe feluri.")
            return
        dlg.on_applied = self.on_applied
        dlg.exec()


class FindInLibraryDialog(QDialog):
    """O piesă lipsă (ex. din playlistul Traktor, cu numele vechi): o cauți în bibliotecă după
    numele curățat și alegi fișierul corect."""

    def __init__(self, missing, items, parent=None, duration: float = 0):
        super().__init__(parent)
        self.setWindowTitle("Caută piesa în bibliotecă")
        self.resize(900, 520)
        self.items = items
        self.duration = duration
        self.chosen = None
        info = QLabel(f"Lipsește: <b>{os.path.basename(missing.path)}</b><br><span style='color:gray'>"
                      f"{missing.path}</span><br>Am curățat numele (fără numărul piesei). Poți modifica "
                      "textul căutării; alege apoi piesa corectă și apasă „Folosește piesa asta”.")
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.query = QLineEdit(ldb.clean_lookup_name(missing.path))
        self.query.textChanged.connect(self.search)
        self.table = _table(["Artist", "Titlu", "Durată", "Fișier"], (200, 260, 60))
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.doubleClicked.connect(lambda *_: self.accept_choice())
        self.status = QLabel()
        bb = QDialogButtonBox()
        self.b_ok = bb.addButton("Folosește piesa asta", QDialogButtonBox.AcceptRole)
        bb.addButton("Renunță", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self.accept_choice)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.query)
        lay.addWidget(self.table, 1)
        lay.addWidget(self.status)
        lay.addWidget(bb)
        self.search()

    def search(self, *_):
        self.found = [it for _sc, it in ldb.find_by_name(self.query.text(), self.items, self.duration)]
        self.table.setRowCount(len(self.found))
        for i, it in enumerate(self.found):
            t = it.track
            self.table.setItem(i, 0, _item(t.artist))
            self.table.setItem(i, 1, _item(t.display_title))
            self.table.setItem(i, 2, _item(fmt_duration(t.duration)))
            self.table.setItem(i, 3, _item(it.path, tooltip=it.path))
        if self.found:
            self.table.selectRow(0)
        self.status.setText(f"{len(self.found)} potriviri" if self.found else
                            "Nicio potrivire. Încearcă mai puține cuvinte (ex. doar titlul).")
        self.b_ok.setEnabled(bool(self.found))

    def accept_choice(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        self.chosen = self.found[rows[0].row()]
        self.accept()


class TraktorChangesDialog(QDialog):
    """La pornire: ce ai schimbat în Traktor de data trecută (BPM, Key, Rating), cu „Scrie în fișiere”."""

    LABELS = {"bpm": "BPM", "key": "Key", "rating": "Rating"}

    def __init__(self, changes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Schimbări din Traktor")
        self.resize(860, 480)
        self.changes = changes
        self.on_write = None
        info = QLabel(f"De la ultima deschidere ai schimbat în Traktor <b>{len(changes)}</b> valori. Manager-ul le "
                      "folosește deja (cea mai nouă schimbare câștigă). Cu „Scrie în fișiere” ajung și în tagurile "
                      "fișierelor, ca să le vadă Rekordbox / CDJ-urile.")
        info.setWordWrap(True)
        self.table = _table(["", "Piesă", "Ce", "Înainte", "Acum"], (28, 380, 70, 90))
        add_space_toggle(self.table, 0)
        self.table.setRowCount(len(changes))
        for i, (path, field, a, b) in enumerate(changes):
            self.table.setItem(i, 0, _item("", check=True))
            self.table.setItem(i, 1, _item(os.path.basename(path), tooltip=path))
            self.table.setItem(i, 2, _item(self.LABELS.get(field, field)))
            self.table.setItem(i, 3, _item(self._fmt(field, a), "gray"))
            note = ""
            if field == "bpm" and a and b:
                r = float(b) / float(a) if float(a) else 0
                note = "  (dublat)" if abs(r - 2) < 0.05 else ("  (înjumătățit)" if abs(r - 0.5) < 0.02 else "")
            self.table.setItem(i, 4, _item(self._fmt(field, b) + note, "#27ae60"))
        bb = QDialogButtonBox()
        b_write = bb.addButton("Scrie bifatele în fișiere", QDialogButtonBox.AcceptRole)
        bb.addButton("Închide", QDialogButtonBox.RejectRole)
        b_write.clicked.connect(self.write)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addWidget(self.table, 1)
        lay.addWidget(bb)

    @staticmethod
    def _fmt(field, v):
        if field == "rating":
            return "★" * int(v or 0) or "—"
        if field == "bpm":
            return f"{float(v):g}" if v else "—"
        return str(v or "—")

    def write(self):
        per: dict = {}
        for i, (path, field, _a, b) in enumerate(self.changes):
            if self.table.item(i, 0).checkState() != Qt.Checked or b in ("", None):
                continue
            f = per.setdefault(path, {})
            f[field] = str(int(round(float(b)))) if field == "bpm" else str(b)
        jobs = [(p, f, None) for p, f in per.items()]
        if jobs and self.on_write:
            self.on_write(jobs)
        self.accept()


class BpmKeyDialog(QDialog):
    """BPM / tonalitatea din Manager față de fișier și față de Traktor / Rekordbox.

    - „În fișier”: bifezi ce scriu în taguri (bun pentru piesele noi, Explore, CDJ fără export).
    - „Diferit de Traktor / Rekordbox”: acolo valoarea se corectează în programul DJ
      (ele țin BPM-ul și beatgrid-ul în baza lor; nu recitesc tagul din fișier).
    """

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"BPM & Key — {len(rows)} piese cu diferențe")
        self.resize(1150, 620)
        self.rows = rows
        self.on_write = None
        info = QLabel("<b>Scrie în fișier</b>: valoarea din Manager ajunge în tagurile fișierului. Piesele care sunt "
                      "în Traktor / Rekordbox sunt <b>debifate</b> implicit: acele programe își țin BPM-ul și "
                      "beatgrid-ul în baza lor și nu recitesc tagul — acolo corectezi din programul DJ "
                      "(×2 / ÷2 în editorul de beatgrid). Coloana „Traktor / Rekordbox” îți arată ce au ele.")
        info.setWordWrap(True)
        self.show_f = QComboBox()
        self.show_f.addItem("Arată: tot", "all")
        self.show_f.addItem("Arată: de scris în fișier", "file")
        self.show_f.addItem("Arată: diferit de Traktor / Rekordbox (de corectat acolo)", "dj")
        self.show_f.currentIndexChanged.connect(self._filter)
        self.table = _table(["", "Piesă", "În fișier", "În Manager", "Sursa", "Traktor / Rekordbox"],
                            (28, 380, 110, 110, 130))
        add_space_toggle(self.table, 0)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            it = r["it"]
            fb, fk = r["file"]
            mb, mk = r["mgr"]
            in_dj = bool(r["dj"])
            self.table.setItem(i, 0, _item("", check=(r["to_file"] and not in_dj)) if r["to_file"]
                               else _item(""))
            self.table.setItem(i, 1, _item(f"{it.track.artist} - {it.track.display_title}", tooltip=it.path))
            self.table.setItem(i, 2, _item(f"{fb:g} · {fk or '—'}" if fb else f"— · {fk or '—'}", "gray"))
            self.table.setItem(i, 3, _item(f"{mb:g} · {mk or '—'}" if mb else f"— · {mk or '—'}",
                                           "#27ae60" if r["to_file"] else None))
            self.table.setItem(i, 4, _item(" / ".join(x for x in r["src"] if x)))
            dj = "; ".join(f"{s}: {b:g} · {k or '—'}" for s, b, k in r["dj"]) or "—"
            self.table.setItem(i, 5, _item(dj, "#e0a458" if r["dj_diff"] else "gray",
                                           tooltip="Diferit de Manager — corectează în programul DJ"
                                           if r["dj_diff"] else None))
        sel = QHBoxLayout()
        sel.addWidget(self.show_f)
        b_all = QPushButton("Bifează tot ce se poate scrie")
        b_all.clicked.connect(lambda: self._set(True))
        b_none = QPushButton("Debifează tot")
        b_none.clicked.connect(lambda: self._set(False))
        sel.addWidget(b_all)
        sel.addWidget(b_none)
        sel.addStretch()
        bb = QHBoxLayout()
        bb.addStretch()
        close = QPushButton("Închide")
        close.clicked.connect(self.reject)
        b_write = QPushButton("Scrie bifatele în fișiere")
        b_write.setObjectName("primary")
        b_write.clicked.connect(self.write)
        bb.addWidget(close)
        bb.addWidget(b_write)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(sel)
        lay.addWidget(self.table, 1)
        lay.addLayout(bb)

    def _set(self, on):
        for i, r in enumerate(self.rows):
            if r["to_file"] and not self.table.isRowHidden(i):
                self.table.item(i, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _filter(self):
        f = self.show_f.currentData()
        for i, r in enumerate(self.rows):
            hide = (f == "file" and not r["to_file"]) or (f == "dj" and not r["dj_diff"])
            self.table.setRowHidden(i, hide)

    def write(self):
        jobs = []
        for i, r in enumerate(self.rows):
            c = self.table.item(i, 0)
            if not r["to_file"] or c is None or c.checkState() != Qt.Checked:
                continue
            mb, mk = r["mgr"]
            f = {}
            if mb:
                f["bpm"] = str(int(round(mb)))
            if mk:
                f["key"] = r["it"].key or mk
            if f:
                jobs.append((r["it"].path, f, None))
        if not jobs:
            QMessageBox.information(self, "BPM & Key", "N-ai bifat nimic de scris.")
            return
        if self.on_write:
            self.on_write(jobs)
        self.accept()
